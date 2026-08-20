"""Errore di INVIO visibile allo staff (relay + !stato).

Caso reale: token tutti validi (!stato ✅) ma i clienti IG non ricevono le
risposte. Il motivo esatto del rifiuto di Meta finiva solo nei log Railway:
ora l'ultimo errore di invio (IG e WA) viene registrato con orario e mostrato
nel relay Discord del messaggio fallito e in !stato.
"""
import httpx
import pytest

import instagram.client as igc
import instagram.webhook as igw
import whatsapp.client as wac


class _Resp:
    def __init__(self, status_code=200, text="", url="http://x"):
        self.status_code = status_code
        self.text = text
        self._url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", self._url), response=self)


class _FakeHttp:
    _resp = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        return _FakeHttp._resp


@pytest.fixture(autouse=True)
def _reset():
    igc._last_send_error = ""
    wac._last_send_error = ""


async def test_ig_send_failure_records_error(monkeypatch):
    _FakeHttp._resp = _Resp(400, '{"error":{"message":"(#10) Message failed to send: outside allowed window","code":10}}')
    monkeypatch.setattr(igc.httpx, "AsyncClient", _FakeHttp)
    monkeypatch.setattr(igc, "_token_for_account", lambda a: "tok")
    ok = await igc.send_ig_message("17841452139166980", "u1", "ciao")
    assert ok is False
    assert "outside allowed window" in igc.last_send_error()
    assert "HTTP 400" in igc.last_send_error()


async def test_wa_send_failure_records_error(monkeypatch):
    _FakeHttp._resp = _Resp(401, '{"error":{"message":"Invalid OAuth access token"}}')
    monkeypatch.setattr(wac.httpx, "AsyncClient", _FakeHttp)
    ok = await wac.send_message("39333", "ciao")
    assert ok is False
    assert "Invalid OAuth access token" in wac.last_send_error()


async def test_ig_relay_includes_send_error(monkeypatch):
    # invio fallito → il relay Discord riporta l'errore esatto di Meta
    igw._ig_conversations.clear()
    igc._last_send_error = "18/8 07:30 — HTTP 400 su invio a u2: (#10) outside allowed window"
    notified = {}

    async def _ctx(*a, **k):
        return "", []
    monkeypatch.setattr(igw, "build_rag_context", _ctx)

    async def _gen(**k):
        return "risposta"
    monkeypatch.setattr(igw, "generate_response", _gen)

    async def _send_fail(*a, **k):
        return False
    monkeypatch.setattr(igw, "send_ig_message", _send_fail)

    async def _notify(phone, venue, user_msg, bot_reply, context=None, delivered=True):
        notified.update({"reply": bot_reply, "delivered": delivered})
    monkeypatch.setattr(igw, "notify_conversation", _notify)

    async def _none(*a, **k):
        return None
    monkeypatch.setattr(igw, "notify_escalation", _none)

    await igw.process_ig_message("24588954374135134", "u2", "info?")
    assert notified["delivered"] is False
    assert "Errore invio IG" in notified["reply"]
    assert "outside allowed window" in notified["reply"]


async def test_stato_includes_send_errors(monkeypatch):
    import notifications.token_health as th
    from notifications.discord_bot import handle_stato

    monkeypatch.setattr(th, "_targets", lambda: [])
    igc._last_send_error = "18/8 07:30 — HTTP 400 su invio a u2: (#10) outside allowed window"
    out = await handle_stato()
    assert "Ultimo errore di INVIO Instagram" in out
    assert "outside allowed window" in out


# --- Webhook respinti per firma: visibili in !stato ---

async def test_bad_signature_rejected_and_counted(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    import webhook_security as ws

    monkeypatch.setattr("config.settings.meta_app_secret", "secret-giusto")
    before, _ = ws.signature_reject_stats()

    # senza header firma → 403 + contatore
    r = TestClient(main.app).post("/webhook", json={"object": "whatsapp_business_account"})
    assert r.status_code == 403
    # con firma sbagliata → 403 + contatore
    r2 = TestClient(main.app).post(
        "/webhook", json={"object": "whatsapp_business_account"},
        headers={"X-Hub-Signature-256": "sha256=deadbeef"})
    assert r2.status_code == 403

    after, last = ws.signature_reject_stats()
    assert after == before + 2
    assert last  # orario registrato

    import notifications.token_health as th
    from notifications.discord_bot import handle_stato
    monkeypatch.setattr(th, "_targets", lambda: [])
    out = await handle_stato()
    assert "RESPINTI per firma non valida" in out
    assert "META_APP_SECRET" in out


async def test_second_ig_secret_accepted(monkeypatch):
    # Webhook IG (Instagram Login) firmati col "Segreto dell'app Instagram",
    # diverso dall'App Secret dell'app WhatsApp: la firma è valida se corrisponde
    # a UNO QUALSIASI dei due secret configurati.
    import hashlib as hl, hmac as hm, json as js
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr("config.settings.meta_app_secret", "secret-wa")
    monkeypatch.setattr("config.settings.meta_app_secret_ig", "secret-ig")
    body = js.dumps({"object": "instagram", "entry": []}).encode()

    def _sig(secret):
        return "sha256=" + hm.new(secret.encode(), body, hl.sha256).hexdigest()

    c = TestClient(main.app)
    for secret in ("secret-wa", "secret-ig"):
        r = c.post("/webhook/instagram", content=body,
                   headers={"Content-Type": "application/json",
                            "X-Hub-Signature-256": _sig(secret)})
        assert r.status_code == 200, secret

    r_bad = c.post("/webhook/instagram", content=body,
                   headers={"Content-Type": "application/json",
                            "X-Hub-Signature-256": _sig("secret-sbagliato")})
    assert r_bad.status_code == 403
