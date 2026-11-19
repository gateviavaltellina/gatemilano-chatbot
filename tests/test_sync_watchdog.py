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


async def test_init_background_runs_startup_sync(monkeypatch):
    # Il sync di startup deve partire DIRETTO (asyncio), non come job "date" dello
    # scheduler: run_date naive (UTC) su scheduler Europe/Rome finiva 2 ore nel
    # passato → misfire scartato → su Railway il sync di startup non partiva MAI.
    import asyncio

    calls = []

    async def _fake_sync():
        calls.append(1)
    monkeypatch.setattr(m, "sync_all_venues", _fake_sync)
    monkeypatch.setattr(m.persistence, "load_state", lambda: False)

    async def _no_discord():
        return None
    monkeypatch.setattr(m, "start_discord_bot", _no_discord)

    await m._init_background()
    await asyncio.sleep(0)  # lascia girare il task creato
    m.scheduler.shutdown(wait=False)
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


async def test_startup_is_not_ready_before_calendar_sync_finishes(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    started, release = asyncio.Event(), asyncio.Event()

    async def slow_sync():
        started.set()
        await release.wait()

    async def no_discord():
        pass

    monkeypatch.setattr(m, "_ready", False)
    monkeypatch.setattr(m, "sync_all_venues", slow_sync)
    monkeypatch.setattr(m, "start_discord_bot", no_discord)
    monkeypatch.setattr(m.persistence, "load_state", lambda: False)
    monkeypatch.setattr(m, "scheduler", SimpleNamespace(
        add_job=lambda *a, **k: None, start=lambda: None, get_jobs=lambda: []))
    task = asyncio.create_task(m._init_background())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not m._ready
    finally:
        release.set()
        await task
        await asyncio.sleep(0)
    assert m._ready


def test_health_does_not_route_traffic_to_starting_container(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(m.app)
    monkeypatch.setattr(m, "_ready", False)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "starting"
    monkeypatch.setattr(m, "_ready", True)
    assert client.get("/health").status_code == 200
