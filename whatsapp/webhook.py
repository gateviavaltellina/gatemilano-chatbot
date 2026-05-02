import logging
import asyncio
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from config import settings
from whatsapp.client import send_message, mark_as_read
from venue.detector import VenueDetector
from rag.chromadb_manager import chromadb_manager
from ai.claude_client import generate_response

logger = logging.getLogger(__name__)
router = APIRouter()

# Deduplicazione messaggi in memoria (per MVP — produzione: Redis)
_processed_ids: set[str] = set()
_MAX_PROCESSED = 10_000

# Stato conversazione per utente: {phone: {"venue": str|None, "history": list}}
_conversations: dict[str, dict] = {}
_venue_detector = VenueDetector()

def _get_conversation(phone: str) -> dict:
    if phone not in _conversations:
        _conversations[phone] = {"venue": None, "history": []}
    return _conversations[phone]

def _add_to_history(conv: dict, role: str, content: str, max_history: int):
    conv["history"].append({"role": role, "content": content})
    if len(conv["history"]) > max_history * 2:
        conv["history"] = conv["history"][-max_history * 2:]

@router.get("")
async def verify_webhook(request: Request) -> Response:
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.wa_verify_token:
        logger.info("Webhook verificato con successo")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verifica fallita")

@router.post("")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body non valido")

    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                msg_id = msg.get("id", "")
                if msg_id in _processed_ids:
                    continue
                _processed_ids.add(msg_id)
                if len(_processed_ids) > _MAX_PROCESSED:
                    old = list(_processed_ids)[:_MAX_PROCESSED // 2]
                    for m in old:
                        _processed_ids.discard(m)

                phone = msg.get("from", "")
                text = msg.get("text", {}).get("body", "").strip()
                if not phone or not text:
                    continue
                background_tasks.add_task(process_message, phone, msg_id, text)

    return {"status": "ok"}

async def process_message(phone: str, msg_id: str, text: str):
    await mark_as_read(msg_id)
    conv = _get_conversation(phone)

    # Rileva venue dal messaggio + storia conversazione
    venue = _venue_detector.detect(text, conv.get("venue"), conv.get("history", []))

    if venue is None:
        # Ambiguo: chiedi conferma
        reply = "Ciao! 👋 Stai cercando info su *Gate Milano* o *Gate Sardinia*?"
        await send_message(phone, reply)
        _add_to_history(conv, "user", text, settings.max_history)
        _add_to_history(conv, "assistant", reply, settings.max_history)
        return

    conv["venue"] = venue

    # Recupera contesto RAG
    rag_context = await chromadb_manager.query(venue, text, top_k=settings.rag_top_k)

    # Genera risposta con Claude
    _add_to_history(conv, "user", text, settings.max_history)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],  # escludi l'ultimo (appena aggiunto)
    )
    _add_to_history(conv, "assistant", reply, settings.max_history)

    await send_message(phone, reply)
