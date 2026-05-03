import logging
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from config import settings
from rag.chromadb_manager import chromadb_manager
from ai.claude_client import generate_response
from notifications.discord import notify_conversation
from instagram.client import send_ig_message

router = APIRouter()
_processed_ids: set[str] = set()
_MAX_PROCESSED = 10_000

logger = logging.getLogger(__name__)

_ig_conversations: dict[str, dict] = {}


_SARDINIA_IDS = {"24588954374135134", "17841452139166980"}
_MILANO_IDS = {"35517015101275600", "17841405933946552"}

def _venue_for_account(ig_account_id: str) -> str:
    if ig_account_id in _SARDINIA_IDS:
        return "gate_sardinia"
    if ig_account_id in _MILANO_IDS:
        return "gate_milano"
    logger.warning("IG account ID sconosciuto: %s — default gate_milano", ig_account_id)
    return "gate_milano"


def _get_conversation(ig_account_id: str, sender_id: str) -> dict:
    key = f"ig_{ig_account_id}_{sender_id}"
    if key not in _ig_conversations:
        _ig_conversations[key] = {"history": []}
    return _ig_conversations[key]


def _add_to_history(conv: dict, role: str, content: str) -> None:
    conv["history"].append({"role": role, "content": content})
    if len(conv["history"]) > settings.max_history * 2:
        conv["history"] = conv["history"][-settings.max_history * 2:]


@router.get("")
async def verify_ig_webhook(request: Request) -> Response:
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.wa_verify_token:
        logger.info("Instagram webhook verificato")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verifica fallita")


@router.post("")
async def receive_ig_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body non valido")

    logger.info("IG webhook body: %s", body)

    if body.get("object") != "instagram":
        logger.info("IG webhook ignored: object=%s", body.get("object"))
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        # Formato reale: entry.messaging[]
        events = entry.get("messaging", [])
        # Formato test Meta: entry.changes[].value
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

            if not sender_id or not text or not msg_id:
                continue
            if sender_id == ig_account_id:
                continue  # ignora messaggi inviati da noi stessi

            if msg_id in _processed_ids:
                continue
            _processed_ids.add(msg_id)
            if len(_processed_ids) > _MAX_PROCESSED:
                for k in list(_processed_ids)[:_MAX_PROCESSED // 2]:
                    _processed_ids.discard(k)

            background_tasks.add_task(process_ig_message, ig_account_id, sender_id, text)

    return {"status": "ok"}


async def process_ig_message(ig_account_id: str, sender_id: str, text: str) -> None:
    venue = _venue_for_account(ig_account_id)
    conv = _get_conversation(ig_account_id, sender_id)

    rag_context = await chromadb_manager.query(venue, text, top_k=settings.rag_top_k)

    _add_to_history(conv, "user", text)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],
    )
    _add_to_history(conv, "assistant", reply)

    await send_ig_message(ig_account_id, sender_id, reply)
    await notify_conversation(f"ig:{sender_id[:12]}", venue, text, reply)
