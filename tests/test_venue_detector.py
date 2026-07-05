"""Rilevamento venue da testo (WhatsApp usa un numero condiviso): i toponimi sardi
devono instradare a Gate Sardinia. Caso reale: 'navette da San Teodoro per il gate'
finiva erroneamente su Gate Milano."""
from venue.detector import VenueDetector

d = VenueDetector()


def test_sardinian_towns_route_to_sardinia():
    for msg in [
        "ci sono navette da San Teodoro per il gate?",
        "come arrivo da Olbia?",
        "vengo da Nuoro, quanto ci metto?",
        "transfer da Orosei?",
        "arrivo all'aeroporto Costa Smeralda",
    ]:
        assert d.detect(msg, None, []) == "gate_sardinia", msg


def test_explicit_venue_still_wins():
    assert d.detect("info gate milano", None, []) == "gate_milano"
    assert d.detect("eventi a gate sardegna", None, []) == "gate_sardinia"


def test_ambiguous_stays_none():
    # nessun segnale geografico/venue → ambiguo (il chiamante applica il default)
    assert d.detect("a che ora aprite?", None, []) is None


def test_current_venue_kept_when_ambiguous():
    assert d.detect("quanto costa un drink?", "gate_sardinia", []) == "gate_sardinia"


# --- Fallback LLM (venue/classifier.py) ---

import pytest
from venue import classifier


class _FakeResp:
    def __init__(self, txt):
        self.content = [type("B", (), {"text": txt})()]


def _fake_client(txt):
    class _M:
        async def create(self, **k):
            return _FakeResp(txt)
    return type("C", (), {"messages": _M()})()


@pytest.mark.asyncio
async def test_classifier_parses_verdicts(monkeypatch):
    import ai.claude_client as cc
    monkeypatch.setattr("config.settings.venue_llm_fallback", True)
    for txt, expected in [("sardegna", "gate_sardinia"), ("milano", "gate_milano"),
                          ("sconosciuto", None), ("Sardegna.", "gate_sardinia")]:
        monkeypatch.setattr(cc, "_client", _fake_client(txt))
        assert await classifier.classify_venue("navette da San Teodoro?") == expected


@pytest.mark.asyncio
async def test_classifier_disabled_returns_none(monkeypatch):
    monkeypatch.setattr("config.settings.venue_llm_fallback", False)
    assert await classifier.classify_venue("navette da San Teodoro?") is None


@pytest.mark.asyncio
async def test_classifier_swallows_errors(monkeypatch):
    import ai.claude_client as cc
    monkeypatch.setattr("config.settings.venue_llm_fallback", True)

    class _Boom:
        class messages:
            @staticmethod
            async def create(**k):
                raise RuntimeError("api down")
    monkeypatch.setattr(cc, "_client", _Boom())
    assert await classifier.classify_venue("qualcosa") is None
