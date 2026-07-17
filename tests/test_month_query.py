"""Domande su un intero MESE per nome ("e ad agosto?"): devono mostrare gli eventi di
quel mese. Caso reale (12/7, Gate Sardinia): 30 eventi ad agosto in Sanity, ma il bot
rispondeva "per agosto non ho ancora eventi in programma" perché un mese senza giorno
preciso non attivava alcun lookup (vedeva solo la finestra breve di 14 giorni)."""
import datetime

import pytest

import rag.date_utils as du
from rag.date_utils import extract_query_months
from rag import event_store as es
from rag import context_builder as cb


_FIXED = datetime.datetime(2026, 7, 12, 15, 0, tzinfo=du._ROME)


def _seed(venue, eid, name, dstr):
    ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d")
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event(venue, eid, f"EVENTO: {name}\nData: {dstr}", {
        "type": "event", "source": "sanity", "event_name": name, "date": dstr,
        "date_ts": ts, "venue": venue, "sanity_id": eid, "ticket_url": "",
    })


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    es._store.clear()
    monkeypatch.setattr(du, "business_now", lambda now=None: _FIXED)

    async def _no_tables(*a, **k):
        return ""
    monkeypatch.setattr(cb, "get_vip_tables_via_site", _no_tables)
    monkeypatch.setattr(cb, "get_vip_tables_sardinia", _no_tables)


def test_extract_query_months_italian_english():
    assert extract_query_months("e ad agosto?") == [(2026, 8)]
    assert extract_query_months("eventi a settembre") == [(2026, 9)]
    assert extract_query_months("any events in August?") == [(2026, 8)]
    # mese già passato → anno prossimo
    assert extract_query_months("a gennaio?") == [(2027, 1)]
    # "may" è escluso (troppo ambiguo)
    assert extract_query_months("you may come") == []


def test_extract_query_months_tolerates_typos():
    assert extract_query_months("agosti") == [(2026, 8)]      # 1 lettera
    assert extract_query_months("agsoto") == [(2026, 8)]      # trasposizione
    assert extract_query_months("a settembr") == [(2026, 9)]  # troncato
    # parole comuni non devono agganciare mesi per fuzzy
    for msg in ["informazioni", "vorrei prenotare un tavolo", "accompagnato"]:
        assert extract_query_months(msg) == [], msg


@pytest.mark.asyncio
async def test_month_question_surfaces_month_events():
    _seed("gate_sardinia", "a1", "Perreo XL", "2026-08-01")
    _seed("gate_sardinia", "a2", "Rocco Hunt", "2026-08-11")
    _seed("gate_sardinia", "a3", "Closing Party", "2026-08-29")
    ctx, dates = await cb.build_rag_context("gate_sardinia", "e ad agosto?")
    assert "agosto 2026" in ctx
    assert "Rocco Hunt" in ctx
    assert "Closing Party" in ctx


@pytest.mark.asyncio
async def test_month_question_no_events_no_block():
    # nessun evento nel mese richiesto → nessun blocco mese (il bot dirà che non c'è nulla)
    _seed("gate_sardinia", "s1", "Qualcosa", "2026-09-05")
    ctx, _ = await cb.build_rag_context("gate_sardinia", "eventi ad agosto?")
    assert "EVENTI GATE SARDINIA — August" not in ctx


@pytest.mark.asyncio
async def test_specific_day_not_duplicated_by_month():
    # "15 agosto" dà il giorno preciso: non si inietta anche tutto agosto
    _seed("gate_sardinia", "a15", "Ferragosto Show", "2026-08-15")
    _seed("gate_sardinia", "a20", "Altro", "2026-08-20")
    ctx, dates = await cb.build_rag_context("gate_sardinia", "che c'è il 15 agosto?")
    assert "2026-08-15" in dates
    assert "EVENTI GATE SARDINIA — August" not in ctx  # niente lista mese
    assert "Ferragosto Show" in ctx


@pytest.mark.asyncio
async def test_month_cross_venue():
    # cliente su canale Milano chiede di agosto: eventi di agosto a Sardegna, etichettati
    _seed("gate_sardinia", "a1", "Perreo XL", "2026-08-01")
    ctx, _ = await cb.build_rag_context("gate_milano", "avete qualcosa ad agosto?")
    assert "venue diversa" in ctx.lower()
    assert "Perreo XL" in ctx
