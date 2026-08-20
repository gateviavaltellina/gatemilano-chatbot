"""Vocali in chat: trascrizione (Whisper) → normale pipeline testo.

Prima i vocali WA/IG ricevevano "scrivimi a parole". Ora, con OPENAI_API_KEY
configurata, vengono scaricati, trascritti e trattati come testo; senza chiave
(o se la trascrizione fallisce) resta il fallback gentile di sempre.
"""
from fastapi.testclient import TestClient

import main
import ai.transcribe as tr
import instagram.webhook as igw
import whatsapp.webhook as waw


def _client():
    return TestClient(main.app)


# --- modulo transcribe ---

async def test_transcription_disabled_without_key(monkeypatch):
    monkeypatch.setattr("config.settings.openai_api_key", "")
    assert not tr.transcription_enabled()
    assert await tr.transcribe_audio(b"audio", "audio/ogg") is None


async def test_transcribe_audio_ok(monkeypatch):
    monkeypatch.setattr("config.settings.openai_api_key", "sk-test")

    class _Resp:
        status_code = 200
        def json(self):
            return {"text": " a che ora aprite stasera? "}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            assert "audio/transcriptions" in url
            return _Resp()

    monkeypatch.setattr(tr.httpx, "AsyncClient", _C)
    assert await tr.transcribe_audio(b"\x4f\x67\x67", "audio/ogg") == "a che ora aprite stasera?"


async def test_transcribe_audio_error_returns_none(monkeypatch):
    monkeypatch.setattr("config.settings.openai_api_key", "sk-test")

    class _Resp:
        status_code = 401
        text = "bad key"

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k): return _Resp()

    monkeypatch.setattr(tr.httpx, "AsyncClient", _C)
    assert await tr.transcribe_audio(b"data", "audio/ogg") is None


# --- WhatsApp: routing + pipeline ---

def test_wa_audio_routes_to_transcription(monkeypatch):
    calls = []

    async def _spy(phone, msg_id, media_id):
        calls.append((phone, media_id))
    monkeypatch.setattr(waw, "process_audio_message", _spy)

    body = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"messages": [
        {"id": "vx-1", "from": "39444", "type": "audio", "audio": {"id": "MEDIA-AUD-1", "voice": True}},
    ]}}]}]}
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == [("39444", "MEDIA-AUD-1")]


async def test_wa_voice_transcribed_feeds_text_pipeline(monkeypatch):
    waw._conversations.clear()
    piped = []

    async def _mark(*a, **k): return None
    monkeypatch.setattr(waw, "mark_as_read", _mark)

    async def _url(mid): return "https://cdn.wa/media1"
    monkeypatch.setattr(waw, "get_media_url", _url)

    monkeypatch.setattr("config.settings.openai_api_key", "sk-test")

    async def _fetch(url, headers=None):
        assert "Authorization" in (headers or {})   # download WA autenticato
        return b"OggS...", "audio/ogg"
    monkeypatch.setattr(tr, "fetch_media_bytes", _fetch)

    async def _stt(data, mime): return "vorrei info sui biglietti di sabato"
    monkeypatch.setattr(tr, "transcribe_audio", _stt)

    async def _inner(phone, msg_id, text):
        piped.append((phone, text))
    monkeypatch.setattr(waw, "_process_message", _inner)

    await waw.process_audio_message("39555", "vx-2", "MEDIA-AUD-2")
    assert piped == [("39555", "vorrei info sui biglietti di sabato")]


async def test_wa_voice_without_key_falls_back(monkeypatch):
    waw._conversations.clear()
    monkeypatch.setattr("config.settings.openai_api_key", "")
    sends, notified = [], {}

    async def _mark(*a, **k): return None
    monkeypatch.setattr(waw, "mark_as_read", _mark)

    async def _send(phone, text):
        sends.append(text); return True
    monkeypatch.setattr(waw, "send_message", _send)

    async def _notify(phone, venue, user_msg, bot_reply, delivered=True):
        notified.update({"user_msg": user_msg})
    monkeypatch.setattr(waw, "notify_conversation", _notify)

    await waw.process_audio_message("39666", "vx-3", "MEDIA-AUD-3")
    assert sends and "testo" in sends[0]            # fallback gentile di sempre
    assert "vocale" in notified["user_msg"]


# --- Instagram: routing + pipeline ---

def test_ig_audio_routes_with_audio_url(monkeypatch):
    calls = []

    async def _spy(ig_account_id, sender_id, text, is_story_reply=False,
                   story_image_url=None, chat_image_url=None, chat_audio_url=None):
        calls.append({"sender": sender_id, "audio": chat_audio_url})
    monkeypatch.setattr(igw, "process_ig_message", _spy)

    body = {"object": "instagram", "entry": [{"id": "24588954374135134", "messaging": [{
        "sender": {"id": "u-voice"},
        "message": {"mid": "vx-ig-1", "attachments": [
            {"type": "audio", "payload": {"url": "https://cdn.ig/voice.ogg"}}]},
    }]}]}
    r = _client().post("/webhook/instagram", json=body)
    assert r.status_code == 200
    assert calls == [{"sender": "u-voice", "audio": "https://cdn.ig/voice.ogg"}]


async def test_ig_voice_transcript_becomes_text(monkeypatch):
    igw._ig_conversations.clear()
    captured = {}
    monkeypatch.setattr("config.settings.openai_api_key", "sk-test")

    async def _fetch(url, headers=None):
        return b"OggS...", "audio/ogg"
    monkeypatch.setattr(tr, "fetch_media_bytes", _fetch)

    async def _stt(data, mime): return "quanto costano i tavoli?"
    monkeypatch.setattr(tr, "transcribe_audio", _stt)

    async def _ctx(venue, text, history=None):
        captured["rag_text"] = text
        return "BASE", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _gen(**k):
        captured["user_message"] = k.get("user_message")
        return "ok"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _none(*a, **k): return None
    monkeypatch.setattr(igw, "notify_conversation", _none)
    monkeypatch.setattr(igw, "notify_escalation", _none)

    async def _send(*a, **k): return True
    monkeypatch.setattr(igw, "send_ig_message", _send)

    await igw.process_ig_message("24588954374135134", "u-v2", "", False, None, None,
                                 "https://cdn.ig/v2.ogg")
    # la trascrizione diventa il testo per RAG e per il modello
    assert captured["rag_text"] == "quanto costano i tavoli?"
    assert captured["user_message"] == "quanto costano i tavoli?"


async def test_ig_voice_untranscribable_falls_back(monkeypatch):
    igw._ig_conversations.clear()
    monkeypatch.setattr("config.settings.openai_api_key", "")
    fallback = []

    async def _non_text(ig_account_id, sender_id):
        fallback.append(sender_id)
    monkeypatch.setattr(igw, "process_ig_non_text", _non_text)

    await igw.process_ig_message("24588954374135134", "u-v3", "", False, None, None,
                                 "https://cdn.ig/v3.ogg")
    assert fallback == ["u-v3"]


# --- Milano: patente ora accettata ---

def test_milano_patente_now_accepted():
    from ai.claude_client import build_system_blocks
    s = build_system_blocks("gate_milano", "RAG", "DT")[0]["text"]
    assert "Patente di guida: **ACCETTATA**" in s
    assert "non accettata in nessun caso" not in s
