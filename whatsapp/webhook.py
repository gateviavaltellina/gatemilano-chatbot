import json
import logging
import random
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks

from config import settings
from webhook_security import verify_meta_signature
from whatsapp.client import send_message, send_document, mark_as_read
from venue.detector import VenueDetector
from rag.context_builder import build_rag_context
from ai.claude_client import generate_response
from notifications.discord import notify_conversation, notify_human_message
from notifications.discord_bot import is_human_takeover

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Deduplication (FIFO OrderedDict: oldest entry removed first) ---
_processed_ids: OrderedDict[str, float] = OrderedDict()
_MAX_PROCESSED = 10_000


def _mark_processed(msg_id: str) -> bool:
    """Returns True if the message is new. Evicts oldest entries when full."""
    if msg_id in _processed_ids:
        return False
    _processed_ids[msg_id] = time.time()
    while len(_processed_ids) > _MAX_PROCESSED:
        _processed_ids.popitem(last=False)  # FIFO
    return True


# --- Conversation state ---
_conversations: dict[str, dict] = {}
_venue_detector = VenueDetector()
_CONV_TTL = 86400       # 24h
_LAZY_PRUNE_RATE = 0.02  # 2% chance of lazy cleanup per access


def _get_conversation(phone: str) -> dict:
    now = time.time()
    if random.random() < _LAZY_PRUNE_RATE:
        cutoff = now - _CONV_TTL
        stale = [p for p, c in list(_conversations.items()) if c.get("last_seen", 0) < cutoff]
        for p in stale:
            del _conversations[p]
            _drinklist_sent.discard(p)
    if phone not in _conversations:
        _conversations[phone] = {"venue": None, "history": [], "last_seen": now}
    else:
        _conversations[phone]["last_seen"] = now
    return _conversations[phone]


def prune_conversations() -> int:
    cutoff = time.time() - _CONV_TTL
    stale = [p for p, c in _conversations.items() if c.get("last_seen", 0) < cutoff]
    for p in stale:
        del _conversations[p]
    _drinklist_sent.difference_update(stale)
    return len(stale)


def _add_to_history(conv: dict, role: str, content: str, max_history: int) -> None:
    conv["history"].append({"role": role, "content": content})
    if len(conv["history"]) > max_history * 2:
        conv["history"] = conv["history"][-max_history * 2:]


# --- Drinklist ---
_DRINKLIST_URL = "https://gatemilano-chatbot-production.up.railway.app/static/drinklist_perreo.pdf"
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "drinklist", "bottle", "bottiglia", "minimo", "perreo xl"]
_drinklist_sent: set[str] = set()

# --- Ignored phones ---
_ignored_phones: set[str] | None = None


def _get_ignored_phones() -> set[str]:
    global _ignored_phones
    if _ignored_phones is None:
        raw = settings.wa_ignored_phones or ""
        _ignored_phones = {p.strip() for p in raw.split(",") if p.strip()}
    return _ignored_phones


# --- Webhook endpoints ---

@router.get("")
async def verify_webhook(request: Request) -> Response:
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.wa_verify_token:
        logger.info("Webhook verificato con successo")
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verifica fallita")


@router.post("")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    raw = await verify_meta_signature(request)
    try:
        body = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Body non valido")

    # Instagram DM (shared webhook endpoint)
    if body.get("object") == "instagram":
        from instagram.webhook import process_ig_message
        for entry in body.get("entry", []):
            ig_account_id = entry.get("id", "")
            for messaging in entry.get("messaging", []):
                msg = messaging.get("message", {})
                msg_id = msg.get("mid", "")
                sender_id = messaging.get("sender", {}).get("id", "")
                text = msg.get("text", "").strip()
                ig_bot_ids = {settings.ig_gatemilano_id, settings.ig_gatesardinia_id}
                if not sender_id or not text or not msg_id or sender_id in ig_bot_ids:
                    continue
                if not _mark_processed(msg_id):
                    continue
                background_tasks.add_task(process_ig_message, ig_account_id, sender_id, text)
        return {"status": "ok"}

    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue
                msg_id = msg.get("id", "")
                if not _mark_processed(msg_id):
                    continue
                phone = msg.get("from", "")
                text = msg.get("text", {}).get("body", "").strip()
                if not phone or not text:
                    continue
                background_tasks.add_task(process_message, phone, msg_id, text)

    return {"status": "ok"}


async def process_message(phone: str, msg_id: str, text: str) -> None:
    if phone in _get_ignored_phones():
        logger.debug("Messaggio ignorato da numero bot: %s", phone)
        return
    await mark_as_read(msg_id)
    conv = _get_conversation(phone)

    if is_human_takeover(phone):
        venue = conv.get("venue") or "gate_milano"
        await notify_human_message(phone, venue, text)
        return

    venue = _venue_detector.detect(text, conv.get("venue"), conv.get("history", []))
    if venue is None:
        venue = "gate_milano"
    conv["venue"] = venue

    rag_context, _ = await build_rag_context(venue, text, history=conv.get("history", []))

    _add_to_history(conv, "user", text, settings.max_history)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],
    )
    _add_to_history(conv, "assistant", reply, settings.max_history)

    await send_message(phone, reply)

    lower_text = text.lower()
    lower_reply = reply.lower()
    if phone not in _drinklist_sent and any(t in lower_text or t in lower_reply for t in _DRINKLIST_TRIGGERS):
        await send_document(phone, _DRINKLIST_URL, "Drinklist VIP Perreo.pdf")
        _drinklist_sent.add(phone)

    await notify_conversation(phone, venue, text, reply)
