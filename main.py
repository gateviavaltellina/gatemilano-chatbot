import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from whatsapp.webhook import router as webhook_router
from rag.chromadb_manager import chromadb_manager
from sync.sanity_sync import sync_all_venues
from notifications.discord_bot import start as start_discord_bot

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
    await chromadb_manager.init()
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


@app.get("/health")
async def health():
    return {"status": "ok" if _ready else "starting", "model": settings.model}


@app.post("/sync/xceed")
async def trigger_sync():
    await sync_all_venues()
    return {"status": "sync completato"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
