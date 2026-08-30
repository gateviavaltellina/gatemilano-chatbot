"""Data richiesta senza serata: il bot deve vedere le serate VICINE e l'orizzonte
REALE del calendario.

Caso reale (IG Milano, 30/8): un cliente scrive "compio gli anni il 17 novembre".
Il 17/11 è un martedì senza serata, ma il 14 e il 15/11 il locale è pieno e il
calendario è confermato fino a dicembre. Il bot ha risposto "non ho eventi in
calendario per il weekend del 14-15 novembre" e "la programmazione arriva fino a
metà settembre": due affermazioni FALSE che bruciano una prenotazione di compleanno.
Causa: nel contesto entravano solo i 14 giorni successivi a oggi, quindi il modello
deduceva l'orizzonte del calendario da un elenco parziale.
"""
import datetime

import pytest

import rag.date_utils as du
from rag import event_store as es
from rag import context_builder as cb


_FIXED = datetime.datetime(2026, 8, 30, 15, 0, tzinfo=du._ROME)


def _seed(venue, eid, name, dstr):
    ts = int(
        datetime.datetime.strptime(dstr, "%Y-%m-%d")
        .replace(tzinfo=datetime.timezone.utc)
        .timestamp()
    )
    es.upsert_event(venue, eid, f"EVENTO: {name}", {
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

    # Calendario realistico: serate a settembre (dentro i 14 giorni) e a novembre/
    # dicembre (fuori dalla finestra breve) — come in produzione.
    _seed("gate_milano", "s1", "ARKORE", "2026-09-04")
    _seed("gate_milano", "s2", "ALARICO & YANAMASTE", "2026-09-05")
    _seed("gate_milano", "n1", "MANTIKORE: PER PLEKS", "2026-11-14")
    _seed("gate_milano", "n2", "SATOSHI", "2026-11-15")
    _seed("gate_milano", "n3", "TRYM", "2026-11-27")
    _seed("gate_milano", "d1", "AIRA", "2026-12-22")


@pytest.mark.asyncio
async def test_data_vuota_mostra_le_serate_vicine():
    # Il 17/11 non ha serate: il bot deve comunque vedere il 14 e il 15/11 e poterli
    # proporre, invece di dire che in quel periodo non c'è nulla.
    ctx, dates = await cb.build_rag_context("gate_milano", "compio gli anni il 17 novembre")
    assert "2026-11-17" in dates
    assert "MANTIKORE" in ctx
    assert "SATOSHI" in ctx
    assert "SERATE VICINE" in ctx


@pytest.mark.asyncio
async def test_orizzonte_calendario_dichiarato():
    # L'ultima data confermata (22/12) va dichiarata: senza, il modello deduce
    # l'orizzonte dalla lista dei 14 giorni e annuncia una stagione più corta.
    ctx, _ = await cb.build_rag_context("gate_milano", "e dopo il 17 novembre c'è qualcosa?")
    assert "COPERTURA CALENDARIO" in ctx
    assert "22 dicembre 2026" in ctx


@pytest.mark.asyncio
async def test_serate_vicine_non_ripescano_il_passato():
    # Data passata: la finestra "vicine" non deve proporre serate già svolte come se
    # fossero in programma (le serate passate non restano nemmeno in archivio).
    around = es.get_events_around_compact("gate_milano", "2026-08-28")
    assert around == ""


@pytest.mark.asyncio
async def test_data_con_evento_non_aggiunge_serate_vicine():
    # Se la data richiesta HA una serata, il blocco "vicine" non serve e non deve
    # sporcare il contesto con altre date.
    ctx, _ = await cb.build_rag_context("gate_milano", "che c'è il 14 novembre?")
    assert "MANTIKORE" in ctx
    assert "SERATE VICINE" not in ctx
