"""Ricontrollo annullamenti fuori sync.

Caso reale (Artie 5ive 20/8): lo staff annulla su TicketSMS DOPO l'ultimo sync
(che gira ogni 2 ore) → un cliente chiede "avete annullato?" e il bot risponde
"no, è confermato!" con tanto di link. Il job cancellation_recheck (ogni 10 min)
ricontrolla solo il flag canceled degli eventi TicketSMS in calendario e, se
trova annullamenti nuovi, rilancia subito il sync completo.
"""
import datetime

import pytest

import main
import rag.date_utils as du
from rag import event_store as es
import sync.sanity_sync as ss


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    es._store.clear()
    monkeypatch.setattr(du, "business_now",
                        lambda now=None: datetime.datetime(2026, 8, 11, 15, 0, tzinfo=du._ROME))


def _seed(venue, eid, name, dstr, ticket_url, canceled=False):
    ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d")
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event(venue, eid, f"EVENTO: {name}\nData: {dstr}", {
        "type": "event", "source": "sanity", "event_name": name, "date": dstr,
        "date_ts": ts, "venue": venue, "ticket_url": ticket_url, "canceled": canceled,
    })


def test_active_ticketsms_events_filters():
    _seed("gate_sardinia", "a", "Artie 5ive", "2026-08-20",
          "https://www.ticketsms.it/event/Artie-5ive-Budoni-Gate-Sardinia-20-08-2026")
    # già annullato → non va ricontrollato
    _seed("gate_sardinia", "b", "Akeem", "2026-08-12",
          "https://www.ticketsms.it/event/Akeem-Budoni-Gate-Sardinia-12-08-2026", canceled=True)
    # biglietteria non-TicketSMS → fuori scope
    _seed("gate_sardinia", "c", "Wade", "2026-08-14", "https://www.fourvenues.com/x")
    # evento passato → fuori finestra
    _seed("gate_sardinia", "d", "Vecchio", "2026-07-01", "https://www.ticketsms.it/event/vecchio")
    out = es.get_active_ticketsms_events("gate_sardinia")
    assert out == [("Artie 5ive",
                    "https://www.ticketsms.it/event/Artie-5ive-Budoni-Gate-Sardinia-20-08-2026")]


async def test_find_new_cancellations_detects(monkeypatch):
    _seed("gate_sardinia", "a", "Artie 5ive", "2026-08-20",
          "https://www.ticketsms.it/event/Artie-5ive-Budoni-Gate-Sardinia-20-08-2026")
    _seed("gate_sardinia", "e", "Evento Ok", "2026-08-15",
          "https://www.ticketsms.it/event/Evento-Ok")

    async def _enr(url):
        return {"about": "", "prices_str": "", "canceled": "Artie" in url}
    monkeypatch.setattr(ss, "_fetch_ticketsms_enrichment", _enr)

    assert await ss.find_new_ticketsms_cancellations() == ["Artie 5ive"]


async def test_recheck_triggers_full_sync_only_on_findings(monkeypatch):
    synced = []

    async def _sync():
        synced.append(True)
    monkeypatch.setattr(main, "sync_all_venues", _sync)

    async def _found(days=60):
        return ["Artie 5ive"]
    monkeypatch.setattr(ss, "find_new_ticketsms_cancellations", _found)
    await main.cancellation_recheck()
    assert synced == [True]

    async def _none(days=60):
        return []
    monkeypatch.setattr(ss, "find_new_ticketsms_cancellations", _none)
    await main.cancellation_recheck()
    assert synced == [True]  # nessun nuovo sync se non c'è niente di nuovo


def test_recheck_job_is_scheduled():
    # il job deve essere registrato nello scheduler all'avvio: la costante qui
    # sotto deve restare allineata a main._init_background
    import inspect
    src = inspect.getsource(main._init_background)
    assert "cancellation_recheck" in src
