import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import httpx

import main
import whatsapp.webhook as waw
from whatsapp.concierge_bridge import (
    build_concierge_request,
    request_concierge_reply,
    sign_concierge_request,
    split_whatsapp_reply,
    valid_concierge_bridge_url,
)


META_SECRET = "meta-test-secret"
BRIDGE_SECRET = "bridge-test-secret"
ANDREA = "393405640389"
CUSTOMER = "393331112222"
PHONE_NUMBER_ID = "1021019861105099"
BRIDGE_URL = "https://admin.gatemilano.eu/api/agent/channels/whatsapp"


def _message(mid, phone, text="ciao", mtype="text"):
    message = {"id": mid, "from": phone, "type": mtype}
    if mtype == "text":
        message["text"] = {"body": text}
    return message


def _payload(*messages):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba",
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                    "messages": list(messages),
                },
            }],
        }],
    }


def _signed_post(client, body):
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(META_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )


def test_bridge_url_validation_and_exact_request_contract():
    assert valid_concierge_bridge_url(BRIDGE_URL)
    assert not valid_concierge_bridge_url("http://admin.test/api/agent/channels/whatsapp")
    assert not valid_concierge_bridge_url("https://admin.test/webhook")

    raw = build_concierge_request(
        message_id="wamid.123",
        sender_wa_id=ANDREA,
        phone_number_id=PHONE_NUMBER_ID,
        text="Aggiornamento Carl Cox?",
    )
    assert json.loads(raw) == {
        "version": 1,
        "messageId": "wamid.123",
        "senderWaId": ANDREA,
        "externalConversationId": ANDREA,
        "phoneNumberId": PHONE_NUMBER_ID,
        "isGroup": False,
        "text": "Aggiornamento Carl Cox?",
    }

    long_reply = ("x" * 3_000) + " " + ("y" * 600)
    chunks = split_whatsapp_reply(long_reply)
    assert len(chunks) == 2
    assert all(len(chunk) <= 3_500 for chunk in chunks)
    assert " ".join(chunks) == long_reply


async def test_bridge_signs_exact_bytes_and_returns_reply():
    seen = {}

    async def handler(request: httpx.Request):
        raw = await request.aread()
        seen["raw"] = raw
        seen["timestamp"] = request.headers["X-Gate-Bridge-Timestamp"]
        seen["signature"] = request.headers["X-Gate-Bridge-Signature"]
        return httpx.Response(200, json={"status": "completed", "reply": "Venduti 123."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reply = await request_concierge_reply(
            bridge_url=BRIDGE_URL,
            bridge_secret=BRIDGE_SECRET,
            message_id="wamid.bridge",
            sender_wa_id=ANDREA,
            phone_number_id=PHONE_NUMBER_ID,
            text="A quanto siamo?",
            client=client,
            now_seconds=1_787_310_000,
        )

    assert reply == "Venduti 123."
    assert seen["timestamp"] == "1787310000"
    assert seen["signature"] == sign_concierge_request(
        seen["raw"], BRIDGE_SECRET, seen["timestamp"]
    )


async def test_bridge_rejects_error_reply():
    async def error_handler(request: httpx.Request):
        return httpx.Response(503, json={"error": "unavailable"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
        reply = await request_concierge_reply(
            bridge_url=BRIDGE_URL,
            bridge_secret=BRIDGE_SECRET,
            message_id="wamid.error",
            sender_wa_id=ANDREA,
            phone_number_id=PHONE_NUMBER_ID,
            text="Report?",
            client=client,
        )
    assert reply is None


def test_andrea_uses_canonical_concierge_and_never_customer_or_legacy_router(monkeypatch):
    concierge_calls = []
    customer_calls = []
    legacy_calls = []
    sent = []

    async def concierge(**kwargs):
        concierge_calls.append(kwargs)
        return "Carl Cox: 123 venduti, aggiornamento delle 12:30."

    async def customer(phone, msg_id, text):
        customer_calls.append((phone, msg_id, text))

    async def legacy(url, raw, signature):
        legacy_calls.append((url, raw, signature))
        return True

    async def send(phone, text):
        sent.append((phone, text))
        return True

    async def read(msg_id):
        return None

    monkeypatch.setattr("config.settings.meta_app_secret", META_SECRET)
    monkeypatch.setattr("config.settings.wa_staff_phones", ANDREA)
    monkeypatch.setattr("config.settings.wa_concierge_phones", ANDREA)
    monkeypatch.setattr("config.settings.wa_staff_assistant_webhook_url", "https://legacy.test/webhook")
    monkeypatch.setattr("config.settings.whatsapp_concierge_bridge_url", BRIDGE_URL)
    monkeypatch.setattr("config.settings.whatsapp_concierge_bridge_secret", BRIDGE_SECRET)
    monkeypatch.setattr(waw, "request_concierge_reply", concierge)
    monkeypatch.setattr(waw, "process_message", customer)
    monkeypatch.setattr(waw, "forward_staff_webhook", legacy)
    monkeypatch.setattr(waw, "send_message", send)
    monkeypatch.setattr(waw, "mark_as_read", read)

    response = _signed_post(
        TestClient(main.app),
        _payload(_message("andrea-canonical-unique", ANDREA, "A quanto siamo con Carl Cox?")),
    )

    assert response.status_code == 200
    assert customer_calls == []
    assert legacy_calls == []
    assert len(concierge_calls) == 1
    assert concierge_calls[0]["sender_wa_id"] == ANDREA
    assert concierge_calls[0]["phone_number_id"] == PHONE_NUMBER_ID
    assert sent == [(ANDREA, "Carl Cox: 123 venduti, aggiornamento delle 12:30.")]


def test_concierge_failure_is_fail_closed_with_explicit_reply(monkeypatch):
    customer_calls = []
    sent = []

    async def concierge(**kwargs):
        return None

    async def customer(phone, msg_id, text):
        customer_calls.append((phone, msg_id, text))

    async def send(phone, text):
        sent.append((phone, text))
        return True

    async def read(msg_id):
        return None

    monkeypatch.setattr("config.settings.meta_app_secret", META_SECRET)
    monkeypatch.setattr("config.settings.wa_staff_phones", "")
    monkeypatch.setattr("config.settings.wa_concierge_phones", ANDREA)
    monkeypatch.setattr(waw, "request_concierge_reply", concierge)
    monkeypatch.setattr(waw, "process_message", customer)
    monkeypatch.setattr(waw, "send_message", send)
    monkeypatch.setattr(waw, "mark_as_read", read)

    response = _signed_post(
        TestClient(main.app),
        _payload(_message("andrea-failure-unique", ANDREA, "A quanto siamo?")),
    )

    assert response.status_code == 200
    assert customer_calls == []
    assert sent == [(ANDREA, waw._CONCIERGE_ERROR_REPLY)]


def test_customer_flow_is_unchanged(monkeypatch):
    customer_calls = []

    async def customer(phone, msg_id, text):
        customer_calls.append((phone, msg_id, text))

    monkeypatch.setattr("config.settings.meta_app_secret", META_SECRET)
    monkeypatch.setattr("config.settings.wa_staff_phones", "")
    monkeypatch.setattr("config.settings.wa_concierge_phones", ANDREA)
    monkeypatch.setattr(waw, "process_message", customer)

    response = _signed_post(
        TestClient(main.app),
        _payload(_message("customer-unchanged-unique", CUSTOMER, "Info tavoli?")),
    )

    assert response.status_code == 200
    assert customer_calls == [(CUSTOMER, "customer-unchanged-unique", "Info tavoli?")]
