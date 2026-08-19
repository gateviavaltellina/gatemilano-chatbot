import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import httpx

import main
import whatsapp.webhook as waw
from whatsapp.staff_router import (
    build_staff_forward,
    forward_staff_webhook,
    normalize_wa_id,
    parse_staff_phones,
    valid_staff_webhook_url,
)


SECRET = "router-test-secret"
STAFF = "393291696882"
CUSTOMER = "393331112222"


def _payload(*messages, statuses=None):
    value = {"metadata": {"phone_number_id": "pn"}, "messages": list(messages)}
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}],
    }


def _message(mid, phone, text="ciao", **extra):
    return {"id": mid, "from": phone, "type": "text", "text": {"body": text}, **extra}


def _signed_post(client, body):
    raw = json.dumps(body, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )


def test_phone_normalization_and_allowlist_parsing():
    assert normalize_wa_id("+39 329 169 6882") == STAFF
    assert normalize_wa_id("0039 329 169 6882") == STAFF
    assert parse_staff_phones("+39 329 169 6882, 393477928255") == {
        STAFF,
        "393477928255",
    }


def test_filtered_payload_contains_only_staff_dms_and_statuses():
    body = _payload(
        _message("staff-1", STAFF),
        _message("customer-1", CUSTOMER),
        _message("group-1", STAFF, group_id="group-x"),
        statuses=[{"id": "outbound-1", "status": "delivered"}],
    )

    routed = build_staff_forward(body, {STAFF}, SECRET)
    assert routed is not None
    raw, signature = routed
    filtered = json.loads(raw)
    messages = filtered["entry"][0]["changes"][0]["value"]["messages"]
    assert [message["id"] for message in messages] == ["staff-1"]
    assert filtered["entry"][0]["changes"][0]["value"]["statuses"][0]["id"] == "outbound-1"
    expected = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


def test_no_forward_without_staff_message_status_or_secret():
    customer_only = _payload(_message("customer-2", CUSTOMER))
    assert build_staff_forward(customer_only, {STAFF}, SECRET) is None
    assert build_staff_forward(_payload(_message("staff-2", STAFF)), {STAFF}, "") is None


def test_staff_dm_is_forwarded_and_never_processed_as_customer(monkeypatch):
    forwarded = []
    customer_calls = []

    async def _forward(url, raw, signature):
        forwarded.append((url, json.loads(raw), signature))
        return True

    async def _customer(phone, msg_id, text):
        customer_calls.append((phone, msg_id, text))

    monkeypatch.setattr("config.settings.meta_app_secret", SECRET)
    monkeypatch.setattr("config.settings.wa_staff_phones", STAFF)
    monkeypatch.setattr(
        "config.settings.wa_staff_assistant_webhook_url",
        "https://staff.example/webhook",
    )
    monkeypatch.setattr(waw, "forward_staff_webhook", _forward)
    monkeypatch.setattr(waw, "process_message", _customer)

    body = _payload(
        _message("router-staff-unique", STAFF, "crea una task"),
        _message("router-customer-unique", CUSTOMER, "info tavoli?"),
    )
    response = _signed_post(TestClient(main.app), body)

    assert response.status_code == 200
    assert customer_calls == [(CUSTOMER, "router-customer-unique", "info tavoli?")]
    assert len(forwarded) == 1
    routed_messages = forwarded[0][1]["entry"][0]["changes"][0]["value"]["messages"]
    assert [message["from"] for message in routed_messages] == [STAFF]


def test_staff_dm_stays_fail_closed_when_router_url_is_missing(monkeypatch):
    customer_calls = []

    async def _customer(phone, msg_id, text):
        customer_calls.append((phone, msg_id, text))

    monkeypatch.setattr("config.settings.meta_app_secret", SECRET)
    monkeypatch.setattr("config.settings.wa_staff_phones", STAFF)
    monkeypatch.setattr("config.settings.wa_staff_assistant_webhook_url", "")
    monkeypatch.setattr(waw, "process_message", _customer)

    response = _signed_post(
        TestClient(main.app),
        _payload(_message("router-fail-closed-unique", STAFF, "eventi di oggi")),
    )

    assert response.status_code == 200
    assert customer_calls == []


def test_webhook_url_validation_and_forward_headers():
    assert valid_staff_webhook_url("https://staff.example/webhook")
    assert not valid_staff_webhook_url("http://staff.example/webhook")
    assert not valid_staff_webhook_url("https://user:pass@staff.example/webhook")
    assert not valid_staff_webhook_url("https://staff.example/other")


async def test_forward_posts_exact_signed_bytes_without_following_redirects():
    seen = {}

    async def handler(request: httpx.Request):
        seen["body"] = await request.aread()
        seen["signature"] = request.headers.get("X-Hub-Signature-256")
        seen["source"] = request.headers.get("X-Gate-Webhook-Source")
        return httpx.Response(200)

    raw = b'{"object":"whatsapp_business_account"}'
    signature = "sha256=abc"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await forward_staff_webhook(
            "https://staff.example/webhook",
            raw,
            signature,
            client=client,
        )

    assert ok is True
    assert seen == {"body": raw, "signature": signature, "source": "customer-router"}
