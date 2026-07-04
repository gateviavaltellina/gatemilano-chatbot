"""Consegna IG affidabile: split sul limite API (1000 char) e allarme staff
quando l'invio fallisce.

Caso reale: il bot 'rispondeva' (notifica Discord normale) ma Instagram aveva
rifiutato l'invio — il cliente non riceveva nulla e lo staff se n'è accorto
5 ore dopo, rispondendo a mano."""
import pytest

from instagram.client import split_for_ig, _IG_TEXT_LIMIT
import instagram.webhook as igw


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
    # trigger tavolo → drinklist inviata e flag alzato
    conv = igw._get_conversation("24588954374135134", "user2")
    assert conv.get("drinklist_sent") is True
    assert len(_wired["sends"]) == 2
