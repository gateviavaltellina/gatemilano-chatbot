import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from whatsapp.webhook import router as webhook_router
from instagram.webhook import router as ig_router
from sync.sanity_sync import sync_all_venues as _sanity_sync
from sync.xceed_sync import sync_all_venues as _xceed_sync
from notifications.discord_bot import start as start_discord_bot


async def sync_all_venues():
    await _sanity_sync()
    await _xceed_sync()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Rome")
_ready = False


async def _init_background():
    global _ready
    logger.info("Avvio inizializzazione in background...")
    scheduler.add_job(
        sync_all_venues,
        CronTrigger(hour=4, minute=0),
        id="sanity_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_all_venues,
        "date",
        run_date=datetime.now() + timedelta(seconds=30),
        id="sanity_sync_startup",
    )
    scheduler.start()
    asyncio.create_task(start_discord_bot())
    _ready = True
    logger.info("Bot pronto. In ascolto su porta %d", settings.port)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_init_background())
    yield
    scheduler.shutdown()
    logger.info("Bot fermato.")


app = FastAPI(title="Gate Milano WhatsApp Bot", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(webhook_router, prefix="/webhook")
app.include_router(ig_router, prefix="/webhook/instagram")


@app.get("/health")
async def health():
    return {"status": "ok" if _ready else "starting", "model": settings.model}


@app.post("/sync/xceed")
async def trigger_sync():
    await sync_all_venues()
    return {"status": "sync completato"}


@app.post("/webhook/sanity")
async def sanity_webhook(background_tasks: BackgroundTasks):
    """Endpoint per webhook Sanity CMS — sync immediato quando un evento viene modificato."""
    background_tasks.add_task(sync_all_venues)
    return {"status": "sync scheduled"}


@app.get("/debug/events")
async def debug_events():
    from rag.event_store import count, upsert_event, _store
    return {
        "gate_milano": count("gate_milano"),
        "gate_sardinia": count("gate_sardinia"),
        "store_keys": list(_store.keys()),
        "total_docs": {k: len(v) for k, v in _store.items()},
    }


@app.post("/debug/test-store")
async def debug_test_store():
    from rag.event_store import upsert_event, count, _store
    upsert_event("test_venue", "test_001", "Test event doc", {"type": "event", "date_ts": 9999999999})
    return {
        "after_insert_count": count("test_venue"),
        "store_id": id(_store),
    }


@app.post("/debug/sync-trace")
async def debug_sync_trace():
    """Run sanity sync with tracing to find where events are lost."""
    from sync.sanity_sync import _fetch_events, _build_document, _extract_xceed_id, SANITY_PROJECTS
    from rag.event_store import upsert_event, _store
    from config import settings as _settings

    results = {}
    for venue_key, cfg in SANITY_PROJECTS.items():
        events = await _fetch_events(cfg["project_id"], cfg["dataset"])
        results[venue_key] = {
            "fetched": len(events),
            "first_3": [{"id": e.get("_id"), "title": e.get("title"), "date": e.get("date")} for e in events[:3]],
        }
        for event in events:
            sanity_id = event.get("_id", "")
            if not sanity_id:
                continue
            doc, meta = _build_document(event, cfg["label"], {})
            upsert_event(venue_key, sanity_id, doc, meta)
    from rag.event_store import count
    results["count_after"] = {"gate_milano": count("gate_milano"), "gate_sardinia": count("gate_sardinia")}
    return results


@app.get("/debug/sanity")
async def debug_sanity():
    import httpx
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    query = '*[_type == "event" && date >= $today && defined(title)] | order(date asc) { _id, title, date }'
    url = "https://68pz8xfn.api.sanity.io/v2021-10-21/data/query/production"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"query": query, "$today": f'"{today}"'})
            data = r.json()
            results = data.get("result", []) or []
            return {
                "today": today,
                "status_code": r.status_code,
                "event_count": len(results),
                "first_3": results[:3],
            }
    except Exception as e:
        return {"error": str(e), "today": today}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
