import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from whatsapp.webhook import router as webhook_router
from rag.chromadb_manager import chromadb_manager
from sync.xceed_sync import sync_all_venues

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Rome")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Avvio Gate Milano WhatsApp Bot...")
    await chromadb_manager.init()
    scheduler.add_job(
        sync_all_venues,
        CronTrigger(hour=4, minute=0),
        id="xceed_sync",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Bot attivo. In ascolto su porta %d", settings.port)
    yield
    scheduler.shutdown()
    logger.info("Bot fermato.")

app = FastAPI(title="Gate Milano WhatsApp Bot", version="1.0.0", lifespan=lifespan)
app.include_router(webhook_router, prefix="/webhook")

@app.get("/health")
async def health():
    return {"status": "ok", "model": settings.model}

@app.post("/sync/xceed")
async def trigger_sync():
    await sync_all_venues()
    return {"status": "sync completato"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
