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


async def nightly_cleanup():
    from whatsapp.webhook import prune_conversations
    from instagram.webhook import prune_ig_conversations
    wa = prune_conversations()
    ig = prune_ig_conversations()
    logger.info("Cleanup notturno: rimossi %d conversazioni WA e %d IG inattive", wa, ig)

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
    # Sync ogni 2 ore durante la giornata (08-23)
    scheduler.add_job(
        sync_all_venues,
        CronTrigger(hour="8-23", minute=0, step=2),
        id="sanity_sync_hourly",
        replace_existing=True,
    )
    # Sync notturno alle 4
    scheduler.add_job(
        sync_all_venues,
        CronTrigger(hour=4, minute=0),
        id="sanity_sync_night",
        replace_existing=True,
    )
    # Sync startup — attende 60s per dare tempo all'app di essere pronta
    scheduler.add_job(
        nightly_cleanup,
        CronTrigger(hour=4, minute=5),
        id="nightly_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_all_venues,
        "date",
        run_date=datetime.now() + timedelta(seconds=60),
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
    from rag.event_store import count
    return {"gate_milano": count("gate_milano"), "gate_sardinia": count("gate_sardinia")}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
