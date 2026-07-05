from __future__ import annotations
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
from notifications.discord import notify_conversation, notify_human_message, notify_escalation
from notifications.discord_bot import is_human_takeover
from notifications.escalation import detect_sensitive
from notifications.debug_trace import record as _trace

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


# --- Drinklist (venue-aware) ---
# Config e logica condivise coi canali (vedi notifications/drinklist.py). Su WhatsApp
# alleghiamo il PDF; gli alias _DRINKLISTS/_should_send_drinklist restano per compat.
from notifications.drinklist import (  # noqa: E402
    DRINKLISTS as _DRINKLISTS,
    DRINK_MENUS as _DRINK_MENUS,
    should_send_drinklist as _should_send_drinklist,
    should_send_drink_menu as _should_send_drink_menu,
)

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
        from instagram.webhook import process_ig_message, process_ig_non_text
        for entry in body.get("entry", []):
            ig_account_id = entry.get("id", "")
            for messaging in entry.get("messaging", []):
                # Ogni evento è isolato: un payload malformato (es. text null) non
                # deve mai far saltare il parsing degli ALTRI messaggi del batch.
                try:
                    msg = messaging.get("message") or {}
                    msg_id = msg.get("mid") or ""
                    sender_id = (messaging.get("sender") or {}).get("id") or ""
                    text = (msg.get("text") or "").strip()
                    ig_bot_ids = {settings.ig_gatemilano_id, settings.ig_gatesardinia_id}
                    if not sender_id or not msg_id or sender_id in ig_bot_ids or msg.get("is_echo"):
                        continue
                    if not _mark_processed(msg_id):
                        continue
                    if text:
                        background_tasks.add_task(process_ig_message, ig_account_id, sender_id, text)
                    elif msg.get("attachments"):
                        # foto/vocale/condivisione senza testo → fallback gentile,
                        # non silenzio (prima veniva scartato senza risposta)
                        background_tasks.add_task(process_ig_non_text, ig_account_id, sender_id)
                except Exception:
                    logger.exception("IG (endpoint condiviso): evento malformato saltato")
        return {"status": "ok"}

    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                # Ogni messaggio è isolato: un payload malformato non deve mai
                # far perdere gli ALTRI messaggi del batch.
                try:
                    msg_id = msg.get("id") or ""
                    if not msg_id or not _mark_processed(msg_id):
                        continue
                    # Messaggio di gruppo (Cloud API Groups): ha un campo group_id.
                    # Ramo separato: solo comandi staff con prefisso, niente flusso 1-1.
                    group_id = msg.get("group_id") or ""
                    if group_id:
                        from whatsapp.group import process_group_message
                        g_text = (msg.get("text") or {}).get("body") or "" if msg.get("type") == "text" else ""
                        background_tasks.add_task(
                            process_group_message, group_id, msg.get("from") or "", msg_id, g_text
                        )
                        continue
                    phone = msg.get("from") or ""
                    if not phone:
                        continue
                    if msg.get("type") == "text":
                        text = ((msg.get("text") or {}).get("body") or "").strip()
                        if not text:
                            continue
                        background_tasks.add_task(process_message, phone, msg_id, text)
                    else:
                        # vocali/foto/video/ecc.: niente più silenzio — fallback gentile
                        background_tasks.add_task(process_non_text, phone, msg_id, msg.get("type") or "")
                except Exception:
                    logger.exception("WA: messaggio malformato saltato")

    return {"status": "ok"}


# --- Messaggi non testuali (vocali, foto, video, documenti...) ---
_NON_TEXT_LABEL = {
    "audio": "vocale", "voice": "vocale", "image": "foto", "video": "video",
    "document": "documento", "sticker": "sticker", "location": "posizione",
    "contacts": "contatto",
}
_NON_TEXT_FALLBACK = {
    "audio": "Ciao! Qui in chat ti rispondo via testo 🙂 Scrivimi pure la tua domanda (eventi, biglietti, tavoli, orari) e ti aiuto subito.",
    "voice": "Ciao! Qui in chat ti rispondo via testo 🙂 Scrivimi pure la tua domanda (eventi, biglietti, tavoli, orari) e ti aiuto subito.",
}
_NON_TEXT_DEFAULT = "Ciao! Scrivimi pure a parole cosa ti serve (evento, biglietti, tavoli, info) e ti rispondo subito 🙂"


async def process_non_text(phone: str, msg_id: str, mtype: str) -> None:
    if phone in _get_ignored_phones():
        return
    await mark_as_read(msg_id)
    conv = _get_conversation(phone)
    venue = conv.get("venue") or "gate_milano"
    label = _NON_TEXT_LABEL.get(mtype, mtype or "allegato")
    if is_human_takeover(phone):
        await notify_human_message(phone, venue, f"[{label}]")
        return
    reply = _NON_TEXT_FALLBACK.get(mtype, _NON_TEXT_DEFAULT)
    sent = await send_message(phone, reply)
    await notify_conversation(phone, venue, f"[{label} ricevuto]", reply, delivered=sent)


# Rete di sicurezza: se la pipeline (RAG/LLM) esplode, il cliente riceve almeno
# un messaggio di cortesia e lo staff un allarme — mai più messaggi senza risposta.
_ERROR_FALLBACK_REPLY = (
    "Scusami, ho avuto un intoppo tecnico proprio ora 🙏 "
    "Riprova a scrivermi tra qualche minuto, oppure ti risponde lo staff appena possibile."
)


async def process_message(phone: str, msg_id: str, text: str) -> None:
    try:
        await _process_message(phone, msg_id, text)
    except Exception:
        logger.exception("WA: errore processando il messaggio di %s — fallback + alert staff", phone)
        venue = _get_conversation(phone).get("venue") or "gate_milano"
        sent = await send_message(phone, _ERROR_FALLBACK_REPLY)
        await notify_conversation(
            phone, venue, text,
            "[⚠️ ERRORE TECNICO — il bot NON ha risposto alla domanda]\n"
            f"Messaggio di cortesia {'inviato' if sent else 'NON inviato'} al cliente.",
            delivered=False,
        )


async def _process_message(phone: str, msg_id: str, text: str) -> None:
    if phone in _get_ignored_phones():
        logger.debug("Messaggio ignorato da numero bot: %s", phone)
        return
    await mark_as_read(msg_id)
    conv = _get_conversation(phone)
    _trace("wa", phone, text, "ricevuto")

    if is_human_takeover(phone):
        venue = conv.get("venue") or "gate_milano"
        _trace("wa", phone, text, "takeover (bot in pausa)")
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

    sent = await send_message(phone, reply)
    _trace("wa", phone, reply, "risposta", inviata=("SI" if sent else "NO"))

    # Tema sensibile (accessibilità/rimborsi/salute/reclami) → alert staff in parallelo
    sensitive = detect_sensitive(text)
    if sensitive:
        await notify_escalation(phone, venue, text, sensitive)

    lower_text = text.lower()
    lower_reply = reply.lower()
    if sent and _should_send_drinklist(venue, lower_text, lower_reply, phone in _drinklist_sent):
        url, filename = _DRINKLISTS[venue]
        if await send_document(phone, url, filename):
            _drinklist_sent.add(phone)

    # Carta drink (prezzi singoli): su richiesta esplicita, allega il PDF del menu.
    if sent and _should_send_drink_menu(venue, lower_text):
        url, filename = _DRINK_MENUS[venue]
        await send_document(phone, url, filename)

    await notify_conversation(phone, venue, text, reply, delivered=sent)
