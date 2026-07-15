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
async def test_cross_venue_injects_other_venue_policies():
    # cliente su canale Milano chiede di un evento Sardegna: oltre all'evento devono
    # arrivare le POLICY di Sardegna (es. minori accompagnati), così il bot risponde
    # invece di rimandare al sito. Caso reale: 14enni al concerto di Massimo Pericolo.
    _seed("gate_sardinia", "mp-2026-07-30", "Massimo Pericolo", "2026-07-30")
    ctx, dates = await cb.build_rag_context(
        "gate_milano", "al concerto di Massimo Pericolo entrano i 14enni accompagnati?"
    )
    assert dates == ["2026-07-30"]
    assert "INFO E POLICY GATE SARDINIA" in ctx
    # la policy età di Sardegna (16+, sotto i 16 con un genitore) è ora nel contesto
    assert "genitore" in ctx.lower()


@pytest.mark.asyncio
async def test_cross_venue_includes_box_office_policy():
    # caso reale: sul canale Milano chiedono se si comprano i biglietti alla porta per
    # un evento Sardegna (Guè). La policy cassa (previa disponibilità) sta nel KB
    # Sardegna, così arriva cross-venue e il bot non dice "non ho info certe".
    _seed("gate_sardinia", "gue-2026-07-16", "Guè", "2026-07-16")
    ctx, _ = await cb.build_rag_context(
        "gate_milano", "posso comprare i biglietti alla porta per Guè?"
    )
    assert "INFO E POLICY GATE SARDINIA" in ctx
    assert "previa disponibilità" in ctx.lower()


@pytest.mark.asyncio
async def test_no_cross_venue_no_other_policies():
    # se l'evento è della STESSA venue, non si inietta la KB dell'altra
    _seed("gate_milano", "ev-mi", "Massimo Pericolo Milano", "2026-06-29")
    ctx, _ = await cb.build_rag_context("gate_milano", "quando c'è Massimo Pericolo?")
    assert "INFO E POLICY" not in ctx


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
async def test_cross_venue_resolves_artist_date_from_other_venue():
    # "Melons" è in cartellone SOLO a Gate Sardinia (5/7); cliente sul canale Milano.
    # Senza risoluzione cross-venue del nome, il bot direbbe "non in programma".
    _seed("gate_sardinia", "tba-2026-07-05-x", "Lubi, Melons, Overlapa", "2026-07-05")
    ctx, dates = await cb.build_rag_context("gate_milano", "c'è una serata con Melons?")
    assert dates == ["2026-07-05"]
    assert "Lubi, Melons, Overlapa" in ctx
    assert "venue diversa" in ctx.lower()


@pytest.mark.asyncio
async def test_same_venue_name_wins_over_other(monkeypatch):
    # se l'artista è di QUESTA venue, si risolve qui e non si pesca dall'altra
    _seed("gate_milano", "ev-mi", "Notorious Vol.1", "2026-06-29")
    _seed("gate_sardinia", "ev-sa", "Notorious Sardegna", "2026-07-02")
    ctx, dates = await cb.build_rag_context("gate_milano", "quando c'è Notorious?")
    assert dates == ["2026-06-29"]


@pytest.mark.asyncio
async def test_named_event_resolved_even_with_relative_date():
    # Caso reale Perreo XL: l'utente cita l'evento per NOME e dà una data relativa
    # ("questo sabato" → 4 luglio), ma in Sanity l'evento è indicizzato sul giorno
    # adiacente (orario di inizio a cavallo di mezzanotte → date_ts sul 5). La sola
    # data relativa non lo troverebbe; la risoluzione per nome sì. Il bot NON deve
    # più rispondere "non ho i dettagli" quando l'evento è in Sanity.
    _seed("gate_sardinia", "ev-perreo", "Perreo XL", "2026-07-05")
    ctx, dates = await cb.build_rag_context(
        "gate_sardinia", "che differenza c'è tra i biglietti del Perreo XL di questo sabato?"
    )
    # il 4 luglio ("questo sabato") non ha eventi in store, ma il nome risolve il 5
    assert "2026-07-05" in dates
    assert "Perreo XL" in ctx


@pytest.mark.asyncio
async def test_followup_resolves_event_from_history():
    # caso reale: turno prima "stasera Perreo XL a Gate Sardinia", poi "quanto costa
    # l'ingresso?" (senza nominare l'evento). Il follow-up deve ripescare l'evento dai
    # messaggi recenti, così i prezzi/KB dell'altra sede sono nel contesto e il bot non
    # risponde "non ho i dettagli" rimandando al sito sbagliato.
    _seed("gate_sardinia", "perreo-11", "Perreo XL", "2026-06-30")
    history = [
        {"role": "user", "content": "è aperto oggi?"},
        {"role": "assistant", "content": "Stasera a Gate Sardinia c'è Perreo XL."},
    ]
    ctx, dates = await cb.build_rag_context(
        "gate_milano", "quanto costa l'ingresso?", history=history)
    assert dates == ["2026-06-30"]
    assert "Perreo XL" in ctx
    assert "venue diversa" in ctx.lower()


@pytest.mark.asyncio
async def test_no_history_no_spurious_event():
    # senza storia e senza nome nel messaggio, nessun evento spurio agganciato
    _seed("gate_sardinia", "perreo-11", "Perreo XL", "2026-06-30")
    ctx, dates = await cb.build_rag_context("gate_milano", "quanto costa l'ingresso?")
    assert dates == []


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
