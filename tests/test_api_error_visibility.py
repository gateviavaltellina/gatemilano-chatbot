"""Visibilità dell'errore API allo staff.

Quando la chiamata al modello fallisce, il cliente riceve un fallback generico
("Mi dispiace, al momento non riesco a rispondere"), ma lo staff (relay Discord)
deve vedere il MOTIVO reale, così un guasto sistematico (credito esaurito,
modello inesistente, 401) è diagnosticabile invece che opaco.
"""
import ai.claude_client as cc
from ai.claude_client import API_ERROR_FALLBACK_PREFIX, last_api_error
from instagram.webhook import _relay_with_api_error


_FALLBACK = f"{API_ERROR_FALLBACK_PREFIX}. Per assistenza contatta info@gatesardinia.it."


def test_last_api_error_getter(monkeypatch):
    monkeypatch.setattr(cc, "_last_api_error", "BadRequestError: credit balance too low")
    assert last_api_error() == "BadRequestError: credit balance too low"


def test_relay_appends_error_on_fallback(monkeypatch):
    monkeypatch.setattr(cc, "_last_api_error", "NotFoundError: model: claude-xyz")
    relay = _relay_with_api_error(_FALLBACK, _FALLBACK)
    assert "ERRORE API" in relay
    assert "claude-xyz" in relay
    # il testo del cliente resta comunque in cima
    assert relay.startswith(API_ERROR_FALLBACK_PREFIX)


def test_relay_untouched_on_normal_reply(monkeypatch):
    monkeypatch.setattr(cc, "_last_api_error", "qualcosa")
    normal = "Ciao! L'età minima è 16 anni."
    assert _relay_with_api_error(normal, normal) == normal
