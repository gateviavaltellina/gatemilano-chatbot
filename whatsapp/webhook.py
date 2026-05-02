import logging
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks
from config import settings
from whatsapp.client import send_message, send_document, mark_as_read

_DRINKLIST_URL = "https://gatemilano-chatbot-production.up.railway.app/static/drinklist_perreo.pdf"
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "drinklist", "bottle", "bottiglia", "minimo", "perreo xl"]
_drinklist_sent: set[str] = set()  # phone → già inviata in questa sessione
from venue.detector import VenueDetector
from rag.chromadb_manager import chromadb_manager
from ai.claude_client import generate_response
from notifications.discord import notify_conversation, notify_human_message
from notifications.discord_bot import is_human_takeover

_TODAY_TERMS = ["stasera", "stanotte", "oggi", "questa sera", "questa notte", "tonight"]
_TOMORROW_TERMS = ["domani", "domani sera", "domani notte", "tomorrow"]
_IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

def _extract_query_dates(text: str) -> list[str]:
    now = datetime.now(timezone.utc)
    lower = text.lower()
    dates = []
    if any(t in lower for t in _TODAY_TERMS):
        dates.append(now.strftime("%Y-%m-%d"))
    if any(t in lower for t in _TOMORROW_TERMS):
        dates.append((now + timedelta(days=1)).strftime("%Y-%m-%d"))
    # Parsing date italiane esplicite: "15 maggio", "il 15 maggio 2026", ecc.
    import re
    for month_name, month_num in _IT_MONTHS.items():
        pattern = rf"\b(\d{{1,2}})\s+{month_name}(?:\s+(\d{{4}}))?"
        for m in re.finditer(pattern, lower):
            day = int(m.group(1))
            year = int(m.group(2)) if m.group(2) else now.year
            # Se la data è già passata quest'anno, assume anno prossimo
            try:
                from datetime import date
                d = date(year, month_num, day)
                if d < now.date() and not m.group(2):
                    d = date(year + 1, month_num, day)
                dates.append(d.strftime("%Y-%m-%d"))
            except ValueError:
                pass
    return list(dict.fromkeys(dates))  # deduplica mantenendo ordine

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

    # Recupera contesto RAG
    rag_context = await chromadb_manager.query(venue, text, top_k=settings.rag_top_k)

    # Date-aware: per "stasera"/"domani" recupera eventi per data esatta da metadata
    # Cerca anche nell'altra venue (utente potrebbe chiedere di eventi cross-venue)
    other_venue = "gate_sardinia" if venue == "gate_milano" else "gate_milano"
    other_venue_name = "Gate Sardinia" if other_venue == "gate_sardinia" else "Gate Milano"
    date_parts = []
    for date_str in _extract_query_dates(text):
        day_events = chromadb_manager.get_events_for_date(venue, date_str)
        if day_events:
            date_parts.append(day_events)
        other_events = chromadb_manager.get_events_for_date(other_venue, date_str)
        if other_events:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_events}")
    if date_parts:
        rag_context = "\n\n---\n\n".join(date_parts) + ("\n\n---\n\n" + rag_context if rag_context else "")

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
