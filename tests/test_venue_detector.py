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
