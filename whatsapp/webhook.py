import logging
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from config import settings, KNOWLEDGE_DIR
from whatsapp.client import send_message, send_document, mark_as_read
from venue.detector import VenueDetector
from rag.event_store import get_upcoming_events, get_events_for_date
from rag.date_utils import extract_query_dates
from ai.claude_client import generate_response
from notifications.discord import notify_conversation, notify_human_message
from notifications.discord_bot import is_human_takeover

_DRINKLIST_URL = "https://gatemilano-chatbot-production.up.railway.app/static/drinklist_perreo.pdf"
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "drinklist", "bottle", "bottiglia", "minimo", "perreo xl"]
_drinklist_sent: set[str] = set()

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

    # Instagram DM
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
                if msg_id in _processed_ids:
                    continue
                _processed_ids.add(msg_id)
                if len(_processed_ids) > _MAX_PROCESSED:
                    old = list(_processed_ids)[:_MAX_PROCESSED // 2]
                    for m in old:
                        _processed_ids.discard(m)
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

_ignored_phones: set[str] | None = None

def _get_ignored_phones() -> set[str]:
    global _ignored_phones
    if _ignored_phones is None:
        raw = settings.wa_ignored_phones or ""
        _ignored_phones = {p.strip() for p in raw.split(",") if p.strip()}
    return _ignored_phones


async def process_message(phone: str, msg_id: str, text: str):
    if phone in _get_ignored_phones():
        logger.debug("Messaggio ignorato da numero bot: %s", phone)
        return
    await mark_as_read(msg_id)
    conv = _get_conversation(phone)

    # Human takeover attivo: notifica Discord, non rispondere automaticamente
    if is_human_takeover(phone):
        venue = conv.get("venue") or "gate_milano"
        await notify_human_message(phone, venue, text)
        return

    # Rileva venue dal messaggio + storia conversazione
    venue = _venue_detector.detect(text, conv.get("venue"), conv.get("history", []))

    if venue is None:
        # Default a Gate Milano (Gate Sardinia stagionale, apre luglio 2026)
        venue = "gate_milano"

    conv["venue"] = venue

    # Knowledge base statica (letta direttamente dal file markdown)
    static_knowledge = ""
    try:
        static_knowledge = (KNOWLEDGE_DIR / f"{venue}.md").read_text(encoding="utf-8")
    except Exception:
        pass

    # Tutti gli eventi futuri (prossimi 14 giorni), ordinati per data
    upcoming = get_upcoming_events(venue, days=14)

    # Date-aware: per "sabato", "domani", "9 maggio" ecc. inietta eventi del giorno esatto
    other_venue = "gate_sardinia" if venue == "gate_milano" else "gate_milano"
    other_venue_name = "Gate Sardinia" if other_venue == "gate_sardinia" else "Gate Milano"
    date_parts = []
    for date_str in extract_query_dates(text):
        day_events = get_events_for_date(venue, date_str)
        if day_events:
            date_parts.append(day_events)
        other_events = get_events_for_date(other_venue, date_str)
        if other_events:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_events}")

    # Costruisci contesto: date-specific > upcoming > static knowledge
    parts = []
    if date_parts:
        parts.extend(date_parts)
    if upcoming:
        parts.append(upcoming)
    if static_knowledge:
        parts.append(static_knowledge)
    rag_context = "\n\n---\n\n".join(parts)

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

    # Allega drinklist PDF una sola volta per conversazione
    lower_text = text.lower()
    lower_reply = reply.lower()
    if phone not in _drinklist_sent and any(t in lower_text or t in lower_reply for t in _DRINKLIST_TRIGGERS):
        await send_document(phone, _DRINKLIST_URL, "Drinklist VIP Perreo.pdf")
        _drinklist_sent.add(phone)

    await notify_conversation(phone, venue, text, reply)
