import logging
import random
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks

from config import settings
from rag.context_builder import build_rag_context
from ai.claude_client import generate_response
from notifications.discord import notify_conversation, notify_human_message
from notifications.discord_bot import is_human_takeover
from instagram.client import send_ig_message

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Deduplication ---
_processed_ids: OrderedDict[str, float] = OrderedDict()
_MAX_PROCESSED = 10_000


def _mark_processed(msg_id: str) -> bool:
    if msg_id in _processed_ids:
        return False
    _processed_ids[msg_id] = time.time()
    while len(_processed_ids) > _MAX_PROCESSED:
        _processed_ids.popitem(last=False)
    return True


# --- Account → venue mapping ---
_SARDINIA_IDS = {"24588954374135134", "17841452139166980"}
_MILANO_IDS = {"35517015101275600", "17841405933946552"}


def _venue_for_account(ig_account_id: str) -> str:
    if ig_account_id in _SARDINIA_IDS:
        return "gate_sardinia"
    if ig_account_id in _MILANO_IDS:
        return "gate_milano"
    logger.warning("IG account ID sconosciuto: %s — default gate_milano", ig_account_id)
    return "gate_milano"


# --- Conversation state ---
_ig_conversations: dict[str, dict] = {}
_CONV_TTL = 86400
_LAZY_PRUNE_RATE = 0.02


def _get_conversation(ig_account_id: str, sender_id: str) -> dict:
    key = f"ig_{ig_account_id}_{sender_id}"
    now = time.time()
    if random.random() < _LAZY_PRUNE_RATE:
        cutoff = now - _CONV_TTL
        stale = [k for k, c in list(_ig_conversations.items()) if c.get("last_seen", 0) < cutoff]
        for k in stale:
            del _ig_conversations[k]
    if key not in _ig_conversations:
        _ig_conversations[key] = {"history": [], "last_seen": now}
    else:
        _ig_conversations[key]["last_seen"] = now
    return _ig_conversations[key]


def prune_ig_conversations() -> int:
    cutoff = time.time() - _CONV_TTL
    stale = [k for k, c in _ig_conversations.items() if c.get("last_seen", 0) < cutoff]
    for k in stale:
        del _ig_conversations[k]
    return len(stale)


def _add_to_history(conv: dict, role: str, content: str) -> None:
    conv["history"].append({"role": role, "content": content})
    if len(conv["history"]) > settings.max_history * 2:
        conv["history"] = conv["history"][-settings.max_history * 2:]


# --- Webhook endpoints ---

@router.get("")
async def verify_ig_webhook(request: Request) -> Response:
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.wa_verify_token:
        logger.info("Instagram webhook verificato")
        return Response(content=params.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verifica fallita")


@router.post("")
async def receive_ig_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body non valido")

    if body.get("object") != "instagram":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        events = entry.get("messaging", [])
        for change in entry.get("changes", []):
            if change.get("field") == "messages":
                events.append(change.get("value", {}))

        for event in events:
            ig_account_id = (
                event.get("recipient", {}).get("id", "")
                or entry.get("id", "")
            )
            sender_id = event.get("sender", {}).get("id", "")
            msg = event.get("message", {})
            text = msg.get("text", "").strip()
            msg_id = msg.get("mid", "")

            if msg.get("is_echo"):
                continue
            if not sender_id or not text or not msg_id:
                continue
            all_bot_ids = _SARDINIA_IDS | _MILANO_IDS
            if sender_id in all_bot_ids:
                continue
            if not _mark_processed(msg_id):
                continue

            background_tasks.add_task(process_ig_message, ig_account_id, sender_id, text)

    return {"status": "ok"}


async def process_ig_message(ig_account_id: str, sender_id: str, text: str) -> None:
    venue = _venue_for_account(ig_account_id)
    conv = _get_conversation(ig_account_id, sender_id)
    phone = f"ig:{sender_id[:12]}"
    context = {"ig_account_id": ig_account_id, "sender_id": sender_id}

    if is_human_takeover(phone):
        await notify_human_message(phone, venue, text, context)
        return

    rag_context, _ = await build_rag_context(venue, text, history=conv.get("history", []))

    _add_to_history(conv, "user", text)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],
    )
    _add_to_history(conv, "assistant", reply)

    await send_ig_message(ig_account_id, sender_id, reply)
    await notify_conversation(phone, venue, text, reply, context)
