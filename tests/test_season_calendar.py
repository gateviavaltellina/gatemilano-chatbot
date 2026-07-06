"""Domande su stagione/calendario/riapertura devono mostrare i PROSSIMI eventi in
calendario anche oltre la finestra breve dei 14 giorni. Caso reale Gate Milano (6/7):
'informazioni sull'apertura della stagione invernale, date e orari?' → il bot
rispondeva 'stagione non ancora annunciata' pur avendo 20 eventi (set–dic) in Sanity,
perché il primo evento era a ~60 giorni (fuori dalla finestra dei 14)."""
import datetime

import pytest

import rag.date_utils as du
from rag import event_store as es
from rag import context_builder as cb


_FIXED = datetime.datetime(2026, 7, 6, 15, 0, tzinfo=du._ROME)


def _seed(venue, eid, name, days_ahead):
    ts = es._today_start_utc() + days_ahead * 86400
    dstr = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
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


@pytest.mark.asyncio
async def test_season_question_surfaces_events_beyond_14_days():
    # eventi della prossima stagione, tutti oltre i 14 giorni
    _seed("gate_milano", "ev1", "Lilya Mandre", 60)
    _seed("gate_milano", "ev2", "Carl Cox", 75)
    _seed("gate_milano", "ev3", "Conway The Machine", 126)
    ctx, _ = await cb.build_rag_context(
        "gate_milano", "informazioni sull'apertura della stagione invernale, date e orari?"
    )
    assert "PROSSIMI EVENTI IN CALENDARIO" in ctx
    assert "Lilya Mandre" in ctx
    assert "Carl Cox" in ctx


@pytest.mark.asyncio
async def test_season_list_capped_and_chronological():
    for i in range(12):
        _seed("gate_milano", f"ev{i}", f"Artist {i:02d}", 30 + i * 5)
    ctx, _ = await cb.build_rag_context("gate_milano", "che eventi avete in programma?")
    # limite di 8 eventi
    assert ctx.count("Artist ") == 8
    # ordine cronologico: il primo seminato (giorno 30) prima dell'ultimo mostrato
    assert ctx.index("Artist 00") < ctx.index("Artist 07")


@pytest.mark.asyncio
async def test_non_season_question_keeps_14_day_window():
    # una domanda generica NON deve tirare dentro tutta la stagione
    _seed("gate_milano", "ev_far", "Far Event", 60)
    ctx, _ = await cb.build_rag_context("gate_milano", "a che ora aprite il sabato?")
    assert "PROSSIMI EVENTI IN CALENDARIO" not in ctx
    assert "Far Event" not in ctx


@pytest.mark.asyncio
async def test_season_question_no_events_no_calendar_block():
    # nessun evento in store → nessun blocco calendario (il bot dirà che non è uscito)
    ctx, _ = await cb.build_rag_context("gate_milano", "quando riapre la stagione?")
    assert "PROSSIMI EVENTI IN CALENDARIO" not in ctx
