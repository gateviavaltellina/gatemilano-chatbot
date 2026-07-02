"""Watchdog: se lo store eventi di una venue resta vuoto (startup sync fallito),
si risincronizza ogni 10 minuti invece di aspettare il cron da 2 ore."""
from rag import event_store as es
import main as m


def setup_function(_):
    es._store.clear()


async def test_watchdog_resyncs_when_store_empty(monkeypatch):
    calls = []

    async def _fake_sync():
        calls.append(1)
    monkeypatch.setattr(m, "sync_all_venues", _fake_sync)

    await m.sync_watchdog()
    assert calls == [1]


async def test_watchdog_noop_when_populated(monkeypatch):
    for v in ("gate_milano", "gate_sardinia"):
        es.upsert_event(v, f"ev-{v}", "EVENTO: X", {"type": "event", "event_name": "X", "date_ts": 1})

    calls = []

    async def _fake_sync():
        calls.append(1)
    monkeypatch.setattr(m, "sync_all_venues", _fake_sync)

    await m.sync_watchdog()
    assert calls == []
