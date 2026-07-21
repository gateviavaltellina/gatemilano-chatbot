"""Vision sulle storie: il bot scarica l'immagine della storia a cui l'utente
risponde e la passa al modello, così capisce la domanda senza dover chiedere."""
import base64

import ai.claude_client as cc


class _Resp:
    def __init__(self, status_code=200, content=b"", ctype="image/jpeg"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": ctype}


class _FakeClient:
    _resp = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeClient._resp


def _patch_http(monkeypatch, resp):
    _FakeClient._resp = resp
    monkeypatch.setattr(cc.httpx, "AsyncClient", _FakeClient)


async def test_fetch_image_block_ok(monkeypatch):
    _patch_http(monkeypatch, _Resp(200, b"\xff\xd8\xff-jpeg-bytes", "image/jpeg"))
    block = await cc.fetch_image_block("https://cdn/story.jpg")
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    assert base64.standard_b64decode(block["source"]["data"]) == b"\xff\xd8\xff-jpeg-bytes"


async def test_fetch_image_block_rejects_video(monkeypatch):
    # una storia VIDEO non è un frame che la vision può leggere → None (ripiego testuale)
    _patch_http(monkeypatch, _Resp(200, b"....", "video/mp4"))
    assert await cc.fetch_image_block("https://cdn/story.mp4") is None


async def test_fetch_image_block_http_error(monkeypatch):
    _patch_http(monkeypatch, _Resp(404, b"", "image/jpeg"))
    assert await cc.fetch_image_block("https://cdn/x") is None


async def test_fetch_image_block_too_large(monkeypatch):
    _patch_http(monkeypatch, _Resp(200, b"x" * 4_600_000, "image/png"))
    assert await cc.fetch_image_block("https://cdn/big.png") is None


async def test_fetch_image_block_empty_url():
    assert await cc.fetch_image_block("") is None
    assert await cc.fetch_image_block(None) is None


# --- generate_response allega l'immagine all'ultimo messaggio utente ---

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


async def test_generate_response_attaches_image(monkeypatch):
    captured = {}

    class _Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeApiResponse()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(cc, "_client", _Client())
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAA"}}
    out = await cc.generate_response(
        venue="gate_sardinia", user_message="da che età?", rag_context="", history=[], image_block=img)
    assert out == "risposta"
    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert isinstance(last["content"], list)
    assert last["content"][0] == img                      # immagine allegata
    assert "da che età?" in last["content"][1]["text"]     # col testo dell'utente
    assert "storia instagram" in last["content"][1]["text"].lower()


async def test_generate_response_no_image_is_plain_text(monkeypatch):
    captured = {}

    class _Messages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeApiResponse()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(cc, "_client", _Client())
    await cc.generate_response(
        venue="gate_sardinia", user_message="ciao", rag_context="", history=[])
    last = captured["messages"][-1]
    assert last["content"] == "ciao"  # nessuna immagine → contenuto testo semplice
