"""Vision sulle FOTO inviate in chat dal cliente (IG + WhatsApp): il bot le
scarica e le legge (screenshot di biglietti, locandine, ricevute), invece del
vecchio fallback "scrivimi a parole". Distinte dalle story reply (contenuto
nostro): usano CHAT_IMAGE_NOTE, non la nota storia."""
from fastapi.testclient import TestClient

import main
import ai.claude_client as cc
import instagram.webhook as igw
import whatsapp.webhook as waw
import whatsapp.client as wac


def _client():
    return TestClient(main.app)


# --- generate_response: nota giusta per le foto in chat ---

class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Content:
    text = "risposta"


class _FakeApiResponse:
    usage = _Usage()
    content = [_Content()]


def _patch_api(monkeypatch, captured):
    class _Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeApiResponse()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(cc, "_client", _Client())


_IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}}


async def test_generate_response_chat_image_uses_chat_note(monkeypatch):
    captured = {}
    _patch_api(monkeypatch, captured)
    await cc.generate_response(
        venue="gate_sardinia", user_message="è valido questo biglietto?", rag_context="",
        history=[], image_block=_IMG, image_note=cc.CHAT_IMAGE_NOTE)
    txt = captured["messages"][-1]["content"][1]["text"]
    assert "INVIATO QUESTA IMMAGINE" in txt          # nota foto-in-chat
    assert "storia Instagram" not in txt              # NON la nota storia
    assert "è valido questo biglietto?" in txt


async def test_generate_response_photo_without_text_sends_note_only(monkeypatch):
    # foto senza caption: il blocco testo è la sola nota (mai testo vuoto → 400 API)
    captured = {}
    _patch_api(monkeypatch, captured)
    await cc.generate_response(
        venue="gate_sardinia", user_message="", rag_context="",
        history=[], image_block=_IMG, image_note=cc.CHAT_IMAGE_NOTE)
    txt = captured["messages"][-1]["content"][1]["text"]
    assert txt == cc.CHAT_IMAGE_NOTE


async def test_generate_response_default_note_is_still_story(monkeypatch):
    # retrocompatibilità: senza image_note resta la nota storia (path story reply)
    captured = {}
    _patch_api(monkeypatch, captured)
    await cc.generate_response(
        venue="gate_sardinia", user_message="da che età?", rag_context="",
        history=[], image_block=_IMG)
    assert "storia Instagram" in captured["messages"][-1]["content"][1]["text"]


# --- IG: routing del webhook ---

def _spy_ig(monkeypatch):
    calls = []

    async def _spy(ig_account_id, sender_id, text, is_story_reply=False,
                   story_image_url=None, chat_image_url=None):
        calls.append({"sender": sender_id, "text": text, "story": is_story_reply,
                      "chat_url": chat_image_url})
    monkeypatch.setattr(igw, "process_ig_message", _spy)
    return calls


def test_ig_endpoint_routes_chat_photo_to_vision(monkeypatch):
    calls = _spy_ig(monkeypatch)
    body = {
        "object": "instagram",
        "entry": [{
            "id": "24588954374135134",
            "messaging": [{
                "sender": {"id": "u-chatphoto"},
                "message": {"mid": "ci-m1", "attachments": [
                    {"type": "image", "payload": {"url": "https://cdn/foto.jpg"}},
                ]},
            }],
        }],
    }
    r = _client().post("/webhook/instagram", json=body)
    assert r.status_code == 200
    assert calls == [{"sender": "u-chatphoto", "text": "", "story": False,
                      "chat_url": "https://cdn/foto.jpg"}]


def test_ig_shared_endpoint_routes_chat_photo_to_vision(monkeypatch):
    calls = _spy_ig(monkeypatch)
    body = {
        "object": "instagram",
        "entry": [{
            "id": "24588954374135134",
            "messaging": [{
                "sender": {"id": "u-chatphoto2"},
                "message": {"mid": "ci-m2", "text": "è questo l'evento?",
                            "attachments": [{"type": "image", "payload": {"url": "https://cdn/f2.jpg"}}]},
            }],
        }],
    }
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == [{"sender": "u-chatphoto2", "text": "è questo l'evento?", "story": False,
                      "chat_url": "https://cdn/f2.jpg"}]


# --- IG: pipeline foto in chat ---

def _wire_ig(monkeypatch, captured):
    igw._ig_conversations.clear()

    async def _ctx(*a, **k):
        return "BASE_CONTEXT", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _gen(**k):
        captured.update(k)
        return "ok"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _none(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_conversation", _none)
    monkeypatch.setattr(igw, "notify_escalation", _none)

    async def _send_ok(*a, **k):
        return True
    monkeypatch.setattr(igw, "send_ig_message", _send_ok)


async def test_ig_chat_photo_passes_block_and_chat_note(monkeypatch):
    captured = {}
    _wire_ig(monkeypatch, captured)

    async def _fetch(url, headers=None):
        return _IMG
    monkeypatch.setattr(igw, "fetch_image_block", _fetch)

    await igw.process_ig_message(
        "24588954374135134", "u_foto", "", False, None, "https://cdn/foto.jpg")
    assert captured["image_block"] == _IMG
    assert captured["image_note"] == cc.CHAT_IMAGE_NOTE
    assert captured["rag_context"] == "BASE_CONTEXT"  # niente hint storia
    # in history resta il segnaposto, non un messaggio vuoto
    conv = igw._get_conversation("24588954374135134", "u_foto")
    assert conv["history"][0] == {"role": "user", "content": "[foto inviata dal cliente]"}


async def test_ig_chat_photo_unfetchable_without_text_falls_back(monkeypatch):
    captured = {}
    _wire_ig(monkeypatch, captured)

    async def _fetch_none(url, headers=None):
        return None
    monkeypatch.setattr(igw, "fetch_image_block", _fetch_none)

    fallback = []

    async def _non_text(ig_account_id, sender_id):
        fallback.append(sender_id)
    monkeypatch.setattr(igw, "process_ig_non_text", _non_text)

    await igw.process_ig_message(
        "24588954374135134", "u_foto_ko", "", False, None, "https://cdn/scaduta.jpg")
    assert fallback == ["u_foto_ko"]
    assert "image_block" not in captured  # il modello non è stato chiamato


# --- WhatsApp: routing del webhook ---

def test_wa_endpoint_routes_image_to_vision(monkeypatch):
    calls = []

    async def _spy(phone, msg_id, media_id, caption):
        calls.append((phone, media_id, caption))
    monkeypatch.setattr(waw, "process_image_message", _spy)

    body = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {"messages": [
                    {"id": "ci-w1", "from": "3933399", "type": "image",
                     "image": {"id": "MEDIA123", "caption": "questo biglietto vale?"}},
                ]},
            }],
        }],
    }
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == [("3933399", "MEDIA123", "questo biglietto vale?")]


def test_wa_image_without_media_id_gets_fallback(monkeypatch):
    calls = []

    async def _spy(phone, msg_id, mtype):
        calls.append((phone, mtype))
    monkeypatch.setattr(waw, "process_non_text", _spy)

    body = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {"messages": [
                    {"id": "ci-w2", "from": "3933388", "type": "image", "image": {}},
                ]},
            }],
        }],
    }
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == [("3933388", "image")]


# --- WhatsApp: pipeline foto in chat ---

def _wire_wa(monkeypatch, captured, notify):
    waw._conversations.clear()

    async def _mark(*a, **k):
        return None
    monkeypatch.setattr(waw, "mark_as_read", _mark)

    async def _ctx(*a, **k):
        return "BASE_CONTEXT", []
    monkeypatch.setattr(waw, "build_rag_context", _ctx)

    async def _classify(*a, **k):
        return None  # mai chiamare l'LLM vero nei test
    monkeypatch.setattr(waw, "classify_venue", _classify)

    async def _gen(**k):
        captured.update(k)
        return "ok risposta"
    monkeypatch.setattr(waw, "generate_response", _gen)

    async def _send_ok(*a, **k):
        return True
    monkeypatch.setattr(waw, "send_message", _send_ok)

    async def _notify(phone, venue, user_msg, bot_reply, delivered=True):
        notify.update({"user_msg": user_msg, "reply": bot_reply, "delivered": delivered})
    monkeypatch.setattr(waw, "notify_conversation", _notify)


async def test_wa_photo_downloaded_and_passed_to_vision(monkeypatch):
    captured, notify = {}, {}
    _wire_wa(monkeypatch, captured, notify)

    async def _media_url(media_id):
        assert media_id == "MEDIA123"
        return "https://lookaside.fbsbx.com/m/abc"
    monkeypatch.setattr(waw, "get_media_url", _media_url)

    fetched = {}

    async def _fetch(url, headers=None):
        fetched.update({"url": url, "headers": headers})
        return _IMG
    monkeypatch.setattr(waw, "fetch_image_block", _fetch)

    await waw.process_image_message("393331", "wamid1", "MEDIA123", "vale per stasera?")
    # il download del media WhatsApp è autenticato
    assert fetched["url"] == "https://lookaside.fbsbx.com/m/abc"
    assert "Authorization" in (fetched["headers"] or {})
    assert captured["image_block"] == _IMG
    assert captured["image_note"] == cc.CHAT_IMAGE_NOTE
    assert captured["user_message"] == "vale per stasera?"
    assert notify["delivered"] is True
    assert "[📷 foto]" in notify["user_msg"]
    conv = waw._get_conversation("393331")
    assert conv["history"][0] == {"role": "user", "content": "vale per stasera?"}
    assert conv["history"][1] == {"role": "assistant", "content": "ok risposta"}


async def test_wa_photo_without_caption_uses_placeholder_history(monkeypatch):
    captured, notify = {}, {}
    _wire_wa(monkeypatch, captured, notify)

    async def _media_url(media_id):
        return "https://lookaside.fbsbx.com/m/xyz"
    monkeypatch.setattr(waw, "get_media_url", _media_url)

    async def _fetch(url, headers=None):
        return _IMG
    monkeypatch.setattr(waw, "fetch_image_block", _fetch)

    await waw.process_image_message("393332", "wamid2", "MEDIA456", "")
    assert captured["user_message"] == ""
    conv = waw._get_conversation("393332")
    assert conv["history"][0] == {"role": "user", "content": "[foto inviata dal cliente]"}


async def test_wa_photo_unfetchable_falls_back_gently(monkeypatch):
    captured, notify = {}, {}
    _wire_wa(monkeypatch, captured, notify)
    sends = []

    async def _media_url(media_id):
        return None  # media scaduto / errore API
    monkeypatch.setattr(waw, "get_media_url", _media_url)

    async def _send(phone, text):
        sends.append(text)
        return True
    monkeypatch.setattr(waw, "send_message", _send)

    await waw.process_image_message("393333", "wamid3", "MEDIA789", "")
    assert "image_block" not in captured        # il modello non è stato chiamato
    assert sends == [waw._NON_TEXT_DEFAULT]     # fallback gentile, mai silenzio
    assert "non scaricabile" in notify["user_msg"]


# --- whatsapp.client.get_media_url ---

class _MediaResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)

    @property
    def text(self):
        return "err"


class _FakeMediaClient:
    _resp = None
    _seen = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _FakeMediaClient._seen = {"url": url, "headers": headers}
        return _FakeMediaClient._resp


async def test_get_media_url_ok(monkeypatch):
    _FakeMediaClient._resp = _MediaResp(200, {"url": "https://lookaside.fbsbx.com/m/ok"})
    monkeypatch.setattr(wac.httpx, "AsyncClient", _FakeMediaClient)
    url = await wac.get_media_url("MEDIA1")
    assert url == "https://lookaside.fbsbx.com/m/ok"
    assert "MEDIA1" in _FakeMediaClient._seen["url"]
    assert "Authorization" in _FakeMediaClient._seen["headers"]


async def test_get_media_url_error_returns_none(monkeypatch):
    _FakeMediaClient._resp = _MediaResp(404, {})
    monkeypatch.setattr(wac.httpx, "AsyncClient", _FakeMediaClient)
    assert await wac.get_media_url("MEDIA404") is None
    assert await wac.get_media_url("") is None
