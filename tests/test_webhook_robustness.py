"""Robustezza dei webhook: un evento malformato nel batch non deve mai far
perdere gli altri messaggi, e i token Meta scaduti devono generare un alert."""
from fastapi.testclient import TestClient

import main
import instagram.webhook as igw
import whatsapp.webhook as waw
from notifications import token_health as th


def _client():
    return TestClient(main.app)


def _spy_ig(monkeypatch):
    calls = []

    async def _spy(ig_account_id, sender_id, text):
        calls.append((sender_id, text))
    monkeypatch.setattr(igw, "process_ig_message", _spy)
    return calls


def test_ig_malformed_event_does_not_drop_the_batch(monkeypatch):
    calls = _spy_ig(monkeypatch)
    body = {
        "object": "instagram",
        "entry": [{
            "id": "24588954374135134",
            "messaging": [
                # evento malformato: text null, niente attachments
                {"sender": {"id": "u-bad"}, "message": {"mid": "rob-m1", "text": None}},
                # evento rotto peggio: message null
                {"sender": {"id": "u-null"}, "message": None},
                # evento valido DOPO i malformati: deve essere processato
                {"sender": {"id": "u-ok"}, "message": {"mid": "rob-m2", "text": "ciao!"}},
            ],
        }],
    }
    r = _client().post("/webhook/instagram", json=body)
    assert r.status_code == 200
    assert calls == [("u-ok", "ciao!")]


def test_wa_malformed_message_does_not_drop_the_batch(monkeypatch):
    calls = []

    async def _spy(phone, msg_id, text):
        calls.append((phone, text))
    monkeypatch.setattr(waw, "process_message", _spy)

    body = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [
                        None,  # elemento rotto nel batch
                        {"id": "rob-w1", "from": "3933311", "type": "text", "text": None},
                        {"id": "rob-w2", "from": "3933322", "type": "text", "text": {"body": "info?"}},
                    ],
                },
            }],
        }],
    }
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == [("3933322", "info?")]


def test_ig_attachment_on_shared_endpoint_gets_fallback(monkeypatch):
    # foto/vocale senza testo sull'endpoint condiviso: prima veniva scartato in
    # silenzio, ora parte il fallback gentile.
    calls = []

    async def _spy(ig_account_id, sender_id):
        calls.append(sender_id)
    monkeypatch.setattr(igw, "process_ig_non_text", _spy)

    body = {
        "object": "instagram",
        "entry": [{
            "id": "24588954374135134",
            "messaging": [
                {"sender": {"id": "u-photo"},
                 "message": {"mid": "rob-m3", "attachments": [{"type": "image"}]}},
            ],
        }],
    }
    r = _client().post("/webhook", json=body)
    assert r.status_code == 200
    assert calls == ["u-photo"]


# --- token health: alert SOLO al cambio di stato ---

async def test_token_alert_on_transition_only(monkeypatch):
    alerts = []

    async def _alert(text):
        alerts.append(text)
    monkeypatch.setattr(th, "_alert", _alert)
    monkeypatch.setattr(th, "_targets", lambda: [("IG test", "http://x/me", "tok")])
    th._last_status.clear()

    verdicts = iter([True, False, False, None, True])

    async def _ok(url, token):
        return next(verdicts)
    monkeypatch.setattr(th, "_token_ok", _ok)

    await th.check_tokens()          # True: nessun alert
    assert alerts == []
    await th.check_tokens()          # True→False: alert scaduto
    assert len(alerts) == 1 and "SCADUTO" in alerts[0]
    await th.check_tokens()          # False→False: nessun nuovo alert
    assert len(alerts) == 1
    await th.check_tokens()          # None (rete): stato invariato, nessun alert
    assert len(alerts) == 1
    await th.check_tokens()          # False→True: alert di ripristino
    assert len(alerts) == 2 and "ripristinati" in alerts[1]
    assert th.get_token_status() == {"IG test": True}
