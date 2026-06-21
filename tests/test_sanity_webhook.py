"""Protezione dell'endpoint POST /webhook/sanity (sync immediato Sanity)."""
from fastapi.testclient import TestClient

import main


def _client(monkeypatch):
    # evita il sync reale (rete) lanciato come background task
    async def _noop():
        return None
    monkeypatch.setattr(main, "sync_all_venues", _noop)
    return TestClient(main.app)


def test_no_secret_configured_allows(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "")
    r = _client(monkeypatch).post("/webhook/sanity")
    assert r.status_code == 200
    assert r.json() == {"status": "sync scheduled"}


def test_secret_set_missing_token_forbidden(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "s3cret")
    assert _client(monkeypatch).post("/webhook/sanity").status_code == 403


def test_secret_set_wrong_token_forbidden(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "s3cret")
    r = _client(monkeypatch).post("/webhook/sanity", params={"key": "nope"})
    assert r.status_code == 403


def test_secret_set_correct_query_key(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "s3cret")
    r = _client(monkeypatch).post("/webhook/sanity", params={"key": "s3cret"})
    assert r.status_code == 200


def test_secret_set_correct_header(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "s3cret")
    r = _client(monkeypatch).post("/webhook/sanity", headers={"X-Webhook-Secret": "s3cret"})
    assert r.status_code == 200


def test_secret_set_correct_bearer(monkeypatch):
    monkeypatch.setattr("config.settings.sanity_webhook_secret", "s3cret")
    r = _client(monkeypatch).post("/webhook/sanity", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
