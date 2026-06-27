"""Cross-venue: un cliente sul canale di una venue chiede di un evento dell'ALTRA
(caso reale chat 23/6: 'tavoli del 5 luglio al Gate Sardinia, bottiglie incluse?'
arrivato sul numero Milano). Il bot deve avere nel contesto i tavoli dell'altra
venue, ETICHETTATI come venue diversa, così può rispondere senza punt all'email."""
import datetime

import pytest

import rag.date_utils as du
from rag import event_store as es
from rag import context_builder as cb


_FIXED = datetime.datetime(2026, 6, 27, 15, 0, tzinfo=du._ROME)


def _seed(venue, eid, name, dstr):
    ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event(venue, eid, f"EVENTO: {name}", {
        "type": "event", "source": "sanity", "event_name": name, "date": dstr,
        "date_ts": ts, "venue": venue, "sanity_id": eid, "ticket_url": "",
    })


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    es._store.clear()
    monkeypatch.setattr(du, "business_now", lambda now=None: _FIXED)
    # niente rete: stub dei lookup tavoli
    async def _no_milano(*a, **k):
        return ""
    async def _sardinia(sanity_id):
        return f"TAVOLI VIP DISPONIBILI\n- TERRAZZA: 20 liberi (event={sanity_id})"
    monkeypatch.setattr(cb, "get_vip_tables_via_site", _no_milano)
    monkeypatch.setattr(cb, "get_vip_tables_sardinia", _sardinia)


@pytest.mark.asyncio
async def test_cross_venue_tables_injected_and_labeled():
    # cliente sul canale Milano, ma l'evento del 5/7 è a Gate Sardinia
    _seed("gate_sardinia", "tba-2026-07-05-x", "Lubi, Melons, Overlapa", "2026-07-05")
    ctx, dates = await cb.build_rag_context(
        "gate_milano", "per i tavoli del 5 luglio a gate sardinia le bottiglie sono incluse?"
    )
    assert dates == ["2026-07-05"]
    assert "TAVOLI VIP DISPONIBILI" in ctx
    assert "tba-2026-07-05-x" in ctx
    # deve essere etichettato come venue diversa, non spacciato per Milano
    assert "gate sardinia" in ctx.lower()
    assert "venue diversa" in ctx.lower()


@pytest.mark.asyncio
async def test_same_venue_tables_not_labeled_other(monkeypatch):
    # se l'evento è della stessa venue del canale, nessuna etichetta cross-venue
    async def _milano_tables(name, date_iso):
        return "TAVOLI VIP DISPONIBILI\n- Console C1"
    monkeypatch.setattr(cb, "get_vip_tables_via_site", _milano_tables)
    _seed("gate_milano", "ev-mi", "Perreo XL", "2026-06-28")
    ctx, _ = await cb.build_rag_context("gate_milano", "tavoli per il 28 giugno?")
    assert "TAVOLI VIP DISPONIBILI" in ctx
    assert "venue diversa" not in ctx.lower()
