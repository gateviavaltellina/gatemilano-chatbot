"""Eventi annullati — pipeline completa.

Caso reale (Fervo Fluxo 22/7): lo staff annulla su TicketSMS, Sanity resta
indietro → il bot confermava l'evento, dava il link d'acquisto e smentiva i
clienti che segnalavano l'annullamento. Ora l'annullamento viene rilevato
dall'API TicketSMS a ogni sync e propagato a documento, lista compatta, stato
aperto/chiuso e lookup tavoli; una segnalazione del cliente genera un alert staff.
"""
import datetime

import pytest

import rag.date_utils as du
from rag import event_store as es
from rag import context_builder as cb
from sync.sanity_sync import _parse_ticketsms_event, _build_document
from notifications.escalation import detect_sensitive


# --- Rilevamento dal payload TicketSMS ---

def test_parse_detects_canceled():
    data = {"body": [{"list": [{"componentType": "eventDetails", "canceled": True,
                                "description": ""}]}]}
    assert _parse_ticketsms_event(data)["canceled"] is True


def test_parse_active_event_not_canceled():
    data = {"body": [{"list": [{"componentType": "eventDetails", "canceled": False,
                                "description": ""}]}]}
    assert _parse_ticketsms_event(data)["canceled"] is False


# --- Documento e metadata ---

def _build_canceled():
    return _build_document(
        {"_id": "fervo", "title": "Fervo Fluxo", "date": "2026-07-22",
         "ticketUrl": "https://www.ticketsms.it/event/Fervo-Fluxo-Budoni-Gate-Sardinia-22-07-2026"},
        "Gate Sardinia",
        {"about": "", "prices_str": "", "canceled": True},
    )


def test_canceled_document_warns_and_drops_ticket_link():
    doc, meta = _build_canceled()
    assert "EVENTO ANNULLATO" in doc
    assert "ticketsms.it" not in doc            # niente link d'acquisto
    assert "info@gatesardinia.it" in doc        # contatto rimborsi della sede giusta
    assert meta["canceled"] is True
    assert meta["ticket_url"] == ""             # nemmeno nei metadata (compact/tavoli)


def test_active_document_unchanged():
    doc, meta = _build_document(
        {"_id": "mp", "title": "Massimo Pericolo", "date": "2026-07-30",
         "ticketUrl": "https://www.ticketsms.it/event/x"},
        "Gate Sardinia", {"about": "", "prices_str": "", "canceled": False})
    assert "ANNULLATO" not in doc
    assert meta["canceled"] is False
    assert meta["ticket_url"]


# --- Store: compact, stato aperto/chiuso, tavoli ---

def _seed(venue, eid, name, dstr, canceled=False):
    ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d")
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event(venue, eid, f"EVENTO: {name}\nData: {dstr}", {
        "type": "event", "source": "sanity", "event_name": name, "date": dstr,
        "date_ts": ts, "venue": venue, "sanity_id": eid,
        "ticket_url": "" if canceled else "https://tick.et/x", "canceled": canceled,
    })


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    es._store.clear()
    monkeypatch.setattr(du, "business_now",
                        lambda now=None: datetime.datetime(2026, 7, 22, 15, 0, tzinfo=du._ROME))


def test_compact_line_marks_canceled():
    _seed("gate_sardinia", "fervo", "Fervo Fluxo", "2026-07-22", canceled=True)
    out = es.get_upcoming_events_compact("gate_sardinia", days=14)
    assert "ANNULLATO" in out
    assert "tick.et" not in out


def test_has_active_event_ignores_canceled():
    _seed("gate_sardinia", "fervo", "Fervo Fluxo", "2026-07-22", canceled=True)
    assert not es.has_active_event("gate_sardinia", "2026-07-22")
    _seed("gate_sardinia", "altro", "Altro Evento", "2026-07-22")
    assert es.has_active_event("gate_sardinia", "2026-07-22")


def test_vip_candidates_skip_canceled():
    _seed("gate_sardinia", "fervo", "Fervo Fluxo", "2026-07-22", canceled=True)
    _seed("gate_sardinia", "mp", "Massimo Pericolo", "2026-07-30")
    assert [c[0] for c in es.get_vip_candidates("gate_sardinia", "2026-07-22")] == []
    assert [c[0] for c in es.get_vip_candidates("gate_sardinia")] == ["Massimo Pericolo"]


async def test_tonight_closed_if_only_event_canceled():
    # l'unico evento di stasera è annullato → STATO DI STASERA = CHIUSO
    _seed("gate_sardinia", "fervo", "Fervo Fluxo", "2026-07-22", canceled=True)
    ctx, _ = await cb.build_rag_context("gate_sardinia", "a che ora aprite?")
    assert "CHIUSO stasera" in ctx
    # ma il documento dell'evento annullato resta leggibile nella lista compatta
    assert "ANNULLATO" in ctx


# --- Segnalazione del cliente → alert staff ---

def test_customer_cancellation_report_triggers_escalation():
    cats = detect_sensitive("ma dice che è annullato")
    assert any("Annullamento" in c for c in cats)
    assert detect_sensitive("a che ora aprite?") == []
