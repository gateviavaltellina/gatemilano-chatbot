import asyncio
import hmac
import logging
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from whatsapp.webhook import router as webhook_router
from instagram.webhook import router as ig_router
from sync.sanity_sync import sync_all_venues as _sanity_sync
from sync.xceed_sync import sync_all_venues as _xceed_sync
from notifications.discord_bot import start as start_discord_bot
import persistence


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
    # Ripristina lo stato conversazioni salvato (se PERSIST_DIR è configurato),
    # così un riavvio/deploy non azzera storia chat e human takeover.
    persistence.load_state()
    try:
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
        scheduler.add_job(
            nightly_cleanup,
            CronTrigger(hour=4, minute=5),
            id="nightly_cleanup",
            replace_existing=True,
        )
        # Salvataggio periodico dello stato conversazioni (no-op se PERSIST_DIR vuoto)
        scheduler.add_job(
            persistence.save_state,
            CronTrigger(minute="*/5"),
            id="persist_state",
            replace_existing=True,
        )
        scheduler.add_job(
            sync_all_venues,
            "date",
            run_date=datetime.now() + timedelta(seconds=5),
            id="sanity_sync_startup",
        )
        scheduler.start()
        logger.info("Scheduler avviato con %d job", len(scheduler.get_jobs()))
    except Exception:
        logger.exception("Errore avvio scheduler — riprovo sync diretto")
        asyncio.create_task(sync_all_venues())
    asyncio.create_task(start_discord_bot())
    _ready = True
    logger.info("Bot pronto. In ascolto su porta %d", settings.port)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_init_background())
    yield
    persistence.save_state()  # snapshot finale prima dello stop (deploy/riavvio)
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


def _verify_sanity_secret(request: Request, key: str) -> None:
    """Protegge /webhook/sanity con un secret condiviso. Accetta il token via header
    X-Webhook-Secret, Authorization: Bearer <token>, o query ?key=. Se il secret non
    è configurato salta la verifica (retro-compatibile, ma logga un warning)."""
    secret = settings.sanity_webhook_secret
    if not secret:
        logger.warning(
            "SANITY_WEBHOOK_SECRET non configurato — /webhook/sanity NON protetto. "
            "Imposta SANITY_WEBHOOK_SECRET in produzione."
        )
        return
    provided = request.headers.get("X-Webhook-Secret") or key or ""
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth.split(" ", 1)[1]
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=403, detail="secret non valido")


@app.post("/webhook/sanity")
async def sanity_webhook(request: Request, background_tasks: BackgroundTasks, key: str = ""):
    """Endpoint per webhook Sanity CMS — sync immediato quando un evento viene
    pubblicato/modificato, senza aspettare il polling. Protetto da SANITY_WEBHOOK_SECRET."""
    _verify_sanity_secret(request, key)
    background_tasks.add_task(sync_all_venues)
    return {"status": "sync scheduled"}


@app.get("/debug/events")
async def debug_events():
    from rag.event_store import count
    return {"gate_milano": count("gate_milano"), "gate_sardinia": count("gate_sardinia")}


@app.get("/debug/vip")
async def debug_vip(venue: str = "gate_milano", text: str = "vorrei un tavolo vip"):
    from rag.context_builder import build_rag_context
    ctx, dates = await build_rag_context(venue, text)
    lines = [l for l in ctx.split("\n") if "Prenota" in l or "TAVOLI" in l or "NON DISPONIBILE" in l]
    return {"query_dates": dates, "vip_lines": lines, "context_preview": ctx[:800]}


@app.get("/debug/vip/raw")
async def debug_vip_raw():
    import re, httpx
    from rag.vip_tables import _extract_slug_id, _fetch_uuid_for_numeric_id, _fetch_bottleservice
    from rag.event_store import _store, _today_start_utc
    from config import settings

    ticket_url = ""
    today_ts = _today_start_utc()
    for e in sorted(_store.get("gate_milano", []), key=lambda x: x["metadata"].get("date_ts", 0)):
        meta = e["metadata"]
        if meta.get("type") == "event" and meta.get("date_ts", 0) >= today_ts and "xceed" in meta.get("ticket_url", ""):
            ticket_url = meta["ticket_url"]
            break

    if not ticket_url:
        return {"error": "no xceed ticket_url found", "today_ts": today_ts, "events_count": len(_store.get("gate_milano", []))}

    slug, numeric_id = _extract_slug_id(ticket_url)
    uuid = await _fetch_uuid_for_numeric_id(numeric_id, settings.xceed_api_key)
    offers = await _fetch_bottleservice(uuid, settings.xceed_api_key) if uuid else []
    return {
        "ticket_url": ticket_url,
        "slug": slug,
        "numeric_id": numeric_id,
        "uuid": uuid,
        "offers_count": len(offers),
        "first_offer": offers[0] if offers else None,
        "api_key_set": bool(settings.xceed_api_key),
    }


@app.get("/eval/correction-cases")
async def correction_cases_export(key: str = ""):
    """Espone gli eval case approvati (per l'importer locale). Protetto da token."""
    from rag import corrections
    if not settings.eval_export_token:
        raise HTTPException(status_code=404)
    if key != settings.eval_export_token:
        raise HTTPException(status_code=403)
    return {"cases": corrections.get_approved_cases()}


@app.get("/eval/corrections")
async def corrections_export(key: str = ""):
    """Espone le correzioni approvate (regole) per il consolidamento locale. Protetto da token."""
    from rag import corrections
    if not settings.eval_export_token:
        raise HTTPException(status_code=404)
    if key != settings.eval_export_token:
        raise HTTPException(status_code=403)
    return {"corrections": corrections.get_approved_corrections()}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
