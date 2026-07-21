"""Consegna IG affidabile: split sul limite API (1000 char) e allarme staff
quando l'invio fallisce.

Caso reale: il bot 'rispondeva' (notifica Discord normale) ma Instagram aveva
rifiutato l'invio — il cliente non riceveva nulla e lo staff se n'è accorto
5 ore dopo, rispondendo a mano."""
import pytest

from instagram.client import (
    split_for_ig, _IG_TEXT_LIMIT, _send_id_for_account,
    _MILANO_SEND_ID, _SARDINIA_SEND_ID,
)
import instagram.webhook as igw


# --- canonicalizzazione ID di invio (Graph API richiede l'IG business id 17841...) ---

def test_send_id_maps_only_on_facebook_api(monkeypatch):
    # via graph.facebook.com: canonicalizza sull'IG business id
    monkeypatch.setattr("config.settings.ig_api_url", "https://graph.facebook.com/v22.0")
    assert _send_id_for_account("35517015101275600") == _MILANO_SEND_ID
    assert _send_id_for_account("24588954374135134") == _SARDINIA_SEND_ID
    assert _send_id_for_account(_MILANO_SEND_ID) == _MILANO_SEND_ID
    assert _send_id_for_account("999") == "999"


def test_send_id_passthrough_on_instagram_login_api(monkeypatch):
    # via graph.instagram.com (setup nativo): id consegnato dal webhook, invariato
    monkeypatch.setattr("config.settings.ig_api_url", "https://graph.instagram.com/v22.0")
    assert _send_id_for_account("35517015101275600") == "35517015101275600"
    assert _send_id_for_account("24588954374135134") == "24588954374135134"


# --- split_for_ig ---

def test_short_text_single_chunk():
    assert split_for_ig("ciao") == ["ciao"]


def test_empty_text_no_chunks():
    assert split_for_ig("") == []
    assert split_for_ig(None) == []


def test_long_text_chunks_within_limit_and_content_preserved():
    para = "Frase di prova che riempie il paragrafo con un po' di testo utile. "
    text = "\n\n".join(para * 5 for _ in range(6))  # ben oltre i 950
    chunks = split_for_ig(text)
    assert len(chunks) > 1
    assert all(len(c) <= _IG_TEXT_LIMIT for c in chunks)
    # nessuna parola persa (confronto insensibile agli spazi/né newline)
    assert "".join(text.split()) == "".join("".join(chunks).split())


def test_split_prefers_paragraph_boundary():
    p1 = "A" * 500
    p2 = "B" * 700
    chunks = split_for_ig(f"{p1}\n\n{p2}")
    assert chunks[0] == p1  # taglio sul confine di paragrafo, non a metà parola


def test_split_keeps_period_with_first_chunk():
    # sul confine di frase ". " il punto resta col primo blocco, il secondo NON inizia con "."
    text = "A" * 940 + ". " + "B" * 100
    chunks = split_for_ig(text)
    assert len(chunks) == 2
    assert chunks[0].endswith(".")
    assert not chunks[1].startswith(".")
    assert chunks[1] == "B" * 100


# --- il fallimento d'invio deve produrre l'allarme, non una notifica normale ---

@pytest.fixture
def _wired(monkeypatch):
    """process_ig_message con tutte le dipendenze esterne stubate."""
    igw._ig_conversations.clear()
    calls = {"notify": None, "sends": []}

    async def _ctx(*a, **k):
        return "", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _gen(**k):
        return "vorrei un tavolo — ecco le info"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _notify(phone, venue, user_msg, bot_reply, context=None, delivered=True):
        calls["notify"] = {"delivered": delivered, "reply": bot_reply}
    monkeypatch.setattr(igw, "notify_conversation", _notify)

    async def _no_escalation(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_escalation", _no_escalation)
    return calls


async def test_failed_send_flags_not_delivered(_wired, monkeypatch):
    async def _send_fail(*a, **k):
        _wired["sends"].append(a)
        return False
    monkeypatch.setattr(igw, "send_ig_message", _send_fail)

    await igw.process_ig_message("24588954374135134", "user1", "vorrei un tavolo")
    assert _wired["notify"]["delivered"] is False
    # invio fallito → la drinklist NON parte e il flag resta giù (si ritenta al giro dopo)
    conv = igw._get_conversation("24588954374135134", "user1")
    assert not conv.get("drinklist_sent", False)
    assert len(_wired["sends"]) == 1


async def test_successful_send_flags_delivered(_wired, monkeypatch):
    async def _send_ok(*a, **k):
        _wired["sends"].append(a)
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)

    await igw.process_ig_message("24588954374135134", "user2", "vorrei un tavolo")
    assert _wired["notify"]["delivered"] is True
    # trigger tavolo → drinklist inclusa e flag alzato
    conv = igw._get_conversation("24588954374135134", "user2")
    assert conv.get("drinklist_sent") is True
    # UN SOLO messaggio: risposta + link accodato (niente messaggio separato che non parte)
    assert len(_wired["sends"]) == 1
    msg = _wired["sends"][0][2]
    assert "vorrei un tavolo" in msg           # la risposta
    assert "static/drinklist_sardegna.pdf" in msg  # il link accodato


# --- rete di sicurezza: pipeline in errore → cortesia al cliente + allarme staff ---

async def test_pipeline_error_sends_fallback_and_alerts(_wired, monkeypatch):
    async def _boom(**k):
        raise RuntimeError("LLM giù")
    monkeypatch.setattr(igw, "generate_response", _boom)

    async def _send_ok(ig_id, sender, text):
        _wired["sends"].append(text)
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)

    # non deve sollevare: il task in background non muore più in silenzio
    await igw.process_ig_message("24588954374135134", "user3", "info tavoli?")
    assert len(_wired["sends"]) == 1
    assert "intoppo tecnico" in _wired["sends"][0]
    assert _wired["notify"]["delivered"] is False
    assert "ERRORE TECNICO" in _wired["notify"]["reply"]


async def test_wa_pipeline_error_sends_fallback_and_alerts(monkeypatch):
    import whatsapp.webhook as waw
    calls = {"notify": None, "sends": []}

    async def _boom(**k):
        raise RuntimeError("LLM giù")
    monkeypatch.setattr(waw, "generate_response", _boom)

    async def _mark(*a, **k):
        return True
    monkeypatch.setattr(waw, "mark_as_read", _mark)

    async def _no_classify(t):
        return None
    monkeypatch.setattr(waw, "classify_venue", _no_classify)

    async def _send_ok(phone, text):
        calls["sends"].append(text)
        return True
    monkeypatch.setattr(waw, "send_message", _send_ok)

    async def _notify(phone, venue, user_msg, bot_reply, context=None, delivered=True):
        calls["notify"] = {"delivered": delivered, "reply": bot_reply}
    monkeypatch.setattr(waw, "notify_conversation", _notify)

    await waw.process_message("393331112223", "wamid.x", "info tavoli?")
    assert len(calls["sends"]) == 1
    assert "intoppo tecnico" in calls["sends"][0]
    assert calls["notify"]["delivered"] is False
    assert "ERRORE TECNICO" in calls["notify"]["reply"]


async def test_story_reply_injects_context_hint(monkeypatch):
    # Risposta a una storia → nel rag_context passato al modello c'è l'hint che spiega
    # di NON assumere che una domanda generica sia sull'ingresso (18+ per lavoro, ecc.).
    igw._ig_conversations.clear()
    captured = {}

    async def _ctx(*a, **k):
        return "BASE_CONTEXT", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _gen(**k):
        captured["rag"] = k.get("rag_context")
        return "ok"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _notify(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_conversation", _notify)

    async def _no_escalation(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_escalation", _no_escalation)

    async def _send_ok(*a, **k):
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)

    await igw.process_ig_message("24588954374135134", "u_story", "da che età?", True)
    assert "STORIA Instagram" in captured["rag"]
    assert "18+" in captured["rag"]
    assert "BASE_CONTEXT" in captured["rag"]  # il contesto normale resta

    captured.clear()
    await igw.process_ig_message("24588954374135134", "u_normal", "da che età?", False)
    assert captured["rag"] == "BASE_CONTEXT"  # nessun hint sui messaggi normali


async def test_story_reply_with_image_passes_block_no_text_hint(monkeypatch):
    # Risposta a una storia con immagine scaricabile → il blocco immagine arriva a
    # generate_response e NON si aggiunge l'hint testuale (il modello vede la storia).
    igw._ig_conversations.clear()
    captured = {}

    async def _ctx(*a, **k):
        return "BASE_CONTEXT", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _fetch(url):
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}}
    monkeypatch.setattr(igw, "fetch_image_block", _fetch)

    async def _gen(**k):
        captured["img"] = k.get("image_block")
        captured["rag"] = k.get("rag_context")
        return "ok"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _notify(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_conversation", _notify)

    async def _no_escalation(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_escalation", _no_escalation)

    async def _send_ok(*a, **k):
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)

    await igw.process_ig_message(
        "24588954374135134", "u_story_img", "da che età?", True, "https://cdn/story.jpg")
    assert captured["img"]["type"] == "image"
    assert captured["rag"] == "BASE_CONTEXT"  # niente hint testuale quando c'è l'immagine


async def test_story_reply_video_falls_back_to_text_hint(monkeypatch):
    # Storia video → fetch ritorna None → si usa l'hint testuale come ripiego.
    igw._ig_conversations.clear()
    captured = {}

    async def _ctx(*a, **k):
        return "BASE_CONTEXT", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _fetch_none(url):
        return None
    monkeypatch.setattr(igw, "fetch_image_block", _fetch_none)

    async def _gen(**k):
        captured["img"] = k.get("image_block")
        captured["rag"] = k.get("rag_context")
        return "ok"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _notify(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_conversation", _notify)

    async def _no_escalation(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_escalation", _no_escalation)

    async def _send_ok(*a, **k):
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)

    await igw.process_ig_message(
        "24588954374135134", "u_story_vid", "da che età?", True, "https://cdn/story.mp4")
    assert captured["img"] is None
    assert "STORIA Instagram" in captured["rag"]  # hint testuale di ripiego
