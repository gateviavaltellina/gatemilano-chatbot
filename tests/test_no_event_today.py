"""Allucinazione reale 8/7: nessun evento oggi, ma il bot rispondeva "stasera c'è
Flaco G" (che era il 9/7), pescando il primo evento della lista PROSSIMI EVENTI e
spacciandolo per stasera. Quando la data richiesta ("oggi"/"stasera") non ha eventi,
il contesto deve dichiararlo esplicitamente."""
import datetime

import pytest

import rag.date_utils as du
from rag import event_store as es
from rag import context_builder as cb


_FIXED = datetime.datetime(2026, 7, 8, 22, 0, tzinfo=du._ROME)


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


@pytest.mark.asyncio
async def test_no_event_today_declared_explicitly():
    # Flaco G è DOMANI (9/7), non oggi (8/7)
    _seed("gate_sardinia", "flaco", "Flaco G", "2026-07-09")
    ctx, dates = await cb.build_rag_context("gate_sardinia", "cosa c'è stasera?")
    assert dates == ["2026-07-08"]
    assert "NESSUN EVENTO" in ctx
    assert "2026-07-08" in ctx
    # l'evento di domani resta in lista (con la sua data), ma marcato come futuro
    assert "Flaco G" in ctx


@pytest.mark.asyncio
async def test_event_today_no_empty_note():
    # se OGGI c'è davvero un evento, nessuna nota "nessun evento"
    _seed("gate_sardinia", "op", "Opening Party", "2026-07-08")
    ctx, dates = await cb.build_rag_context("gate_sardinia", "cosa c'è stasera?")
    assert dates == ["2026-07-08"]
    assert "NESSUN EVENTO" not in ctx
    assert "Opening Party" in ctx


@pytest.mark.asyncio
async def test_no_events_at_all_still_declares_empty():
    # nessun evento in store: la data richiesta va comunque dichiarata vuota
    ctx, _ = await cb.build_rag_context("gate_sardinia", "che c'è oggi?")
    assert "NESSUN EVENTO" in ctx


@pytest.mark.asyncio
async def test_tonight_closed_status_even_without_stasera_word():
    # caso reale: "a che ora inizia a cantare?" (niente "stasera") in una sera SENZA
    # evento → il bot diceva "la serata è in corso 22:00-03:00". Ora lo STATO DI STASERA
    # è sempre iniettato: locale CHIUSO stasera.
    _seed("gate_sardinia", "tue", "Emis Killa", "2026-07-14")  # evento domani, non oggi
    ctx, dates = await cb.build_rag_context("gate_sardinia", "a che ora inizia a cantare?")
    assert dates == []
    assert "STATO DI STASERA" in ctx
    assert "CHIUSO stasera" in ctx


@pytest.mark.asyncio
async def test_tonight_open_status_when_event_today():
    _seed("gate_sardinia", "today", "Perreo XL", "2026-07-08")  # _FIXED è 8/7
    ctx, _ = await cb.build_rag_context("gate_sardinia", "a che ora si inizia?")
    assert "STATO DI STASERA" in ctx
    assert "locale APERTO" in ctx
    assert "CHIUSO stasera" not in ctx
