"""Protezione degli endpoint /debug/*: aperti se DEBUG_KEY non è configurata
(retro-compatibile), altrimenti richiedono ?key= corretto."""
from fastapi.testclient import TestClient

import main


def _client():
    return TestClient(main.app)


def test_debug_open_when_no_key(monkeypatch):
    monkeypatch.setattr("config.settings.debug_key", "")
    r = _client().get("/debug/events")
    assert r.status_code == 200


def test_debug_forbidden_without_key_when_configured(monkeypatch):
    monkeypatch.setattr("config.settings.debug_key", "s3cret")
    assert _client().get("/debug/events").status_code == 403
    assert _client().get("/debug/last-messages").status_code == 403


def test_debug_ok_with_correct_key(monkeypatch):
    monkeypatch.setattr("config.settings.debug_key", "s3cret")
    r = _client().get("/debug/events", params={"key": "s3cret"})
    assert r.status_code == 200


def test_debug_refresh_tokens_mutation_protected(monkeypatch):
    # l'endpoint che MUTA i token non deve essere azionabile senza chiave
    monkeypatch.setattr("config.settings.debug_key", "s3cret")
    assert _client().post("/debug/refresh-tokens").status_code == 403
