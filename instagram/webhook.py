import json
import logging
import random
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks

from config import settings
from webhook_security import verify_meta_signature
from rag.context_builder import build_rag_context
from ai.claude_client import generate_response
from notifications.discord import notify_conversation, notify_human_message, notify_escalation
from notifications.discord_bot import is_human_takeover
from notifications.escalation import detect_sensitive
from notifications.drinklist import should_send_drinklist, drinklist_link_message
from instagram.client import send_ig_message, react_to_message

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
    raw = await verify_meta_signature(request)
    try:
        body = json.loads(raw)
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
            # Ogni evento è isolato: un payload malformato (es. text null) non deve
            # mai far perdere gli ALTRI messaggi del batch.
            try:
                ig_account_id = (
                    (event.get("recipient") or {}).get("id") or ""
                    or entry.get("id") or ""
                )
                sender_id = (event.get("sender") or {}).get("id") or ""
                if not sender_id:
                    continue
                all_bot_ids = _SARDINIA_IDS | _MILANO_IDS
                if sender_id in all_bot_ids:
                    continue

                # Reaction a una nostra storia/messaggio → reagiamo a nostra volta (no testo)
                reaction = event.get("reaction")
                if reaction:
                    rid = reaction.get("mid") or ""
                    if reaction.get("action") == "react" and rid and _mark_processed(f"rx:{rid}:{sender_id}"):
                        background_tasks.add_task(process_ig_reaction, ig_account_id, sender_id, rid)
                    continue

                msg = event.get("message") or {}
                if msg.get("is_echo"):
                    continue
                text = (msg.get("text") or "").strip()
                msg_id = msg.get("mid") or ""
                if not msg_id:
                    continue
                if not _mark_processed(msg_id):
                    continue

                attachments = msg.get("attachments") or []
                att_type = (attachments[0] or {}).get("type", "") if attachments else ""
                if text:
                    background_tasks.add_task(process_ig_message, ig_account_id, sender_id, text)
                elif att_type in ("story_mention", "share"):
                    # menzione/post nella storia → mettiamo un like ❤️ invece di un testo
                    background_tasks.add_task(process_ig_story_mention, ig_account_id, sender_id, msg_id)
                elif attachments:
                    # foto/vocale/video in DM → fallback testuale (qui il cliente cerca aiuto)
                    background_tasks.add_task(process_ig_non_text, ig_account_id, sender_id)
            except Exception:
                logger.exception("IG: evento malformato saltato")

    return {"status": "ok"}


# Rete di sicurezza: se la pipeline (RAG/LLM) esplode, il cliente riceve almeno
# un messaggio di cortesia e lo staff un allarme — mai più messaggi spariti nel
# nulla (task in background morto in silenzio: né risposta né notifica).
_ERROR_FALLBACK_REPLY = (
    "Scusami, ho avuto un intoppo tecnico proprio ora 🙏 "
    "Riprova a scrivermi tra qualche minuto, oppure ti risponde lo staff appena possibile."
)


async def process_ig_message(ig_account_id: str, sender_id: str, text: str) -> None:
    try:
        await _process_ig_message(ig_account_id, sender_id, text)
    except Exception:
        logger.exception("IG: errore processando il messaggio di %s — fallback + alert staff", sender_id)
        venue = _venue_for_account(ig_account_id)
        phone = f"ig:{sender_id[:12]}"
        context = {"ig_account_id": ig_account_id, "sender_id": sender_id}
        sent = await send_ig_message(ig_account_id, sender_id, _ERROR_FALLBACK_REPLY)
        await notify_conversation(
            phone, venue, text,
            "[⚠️ ERRORE TECNICO — il bot NON ha risposto alla domanda]\n"
            f"Messaggio di cortesia {'inviato' if sent else 'NON inviato'} al cliente.",
            context, delivered=False,
        )


async def _process_ig_message(ig_account_id: str, sender_id: str, text: str) -> None:
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

    sent = await send_ig_message(ig_account_id, sender_id, reply)

    # Drinklist: IG non può allegare PDF → invia il LINK come testo. Flag per
    # conversazione (persistito con la conv) per non re-inviarlo a ogni messaggio.
    # Il flag si alza SOLO se l'invio è riuscito, così al giro dopo si ritenta.
    if sent and should_send_drinklist(venue, text.lower(), reply.lower(), conv.get("drinklist_sent", False)):
        link_msg = drinklist_link_message(venue)
        if link_msg and await send_ig_message(ig_account_id, sender_id, link_msg):
            conv["drinklist_sent"] = True

    sensitive = detect_sensitive(text)
    if sensitive:
        await notify_escalation(phone, venue, text, sensitive, context)

    await notify_conversation(phone, venue, text, reply, context, delivered=sent)


async def process_ig_non_text(ig_account_id: str, sender_id: str) -> None:
    """Allegato IG senza testo (foto/vocale/condivisione): risponde a parole
    invece di lasciare l'utente nel vuoto."""
    venue = _venue_for_account(ig_account_id)
    conv = _get_conversation(ig_account_id, sender_id)
    phone = f"ig:{sender_id[:12]}"
    context = {"ig_account_id": ig_account_id, "sender_id": sender_id}
    if is_human_takeover(phone):
        await notify_human_message(phone, venue, "[allegato]", context)
        return
    reply = "Ciao! Scrivimi pure a parole cosa ti serve (evento, biglietti, tavoli, info) e ti rispondo subito 🙂"
    sent = await send_ig_message(ig_account_id, sender_id, reply)
    await notify_conversation(phone, venue, "[allegato ricevuto]", reply, context, delivered=sent)


async def process_ig_story_mention(ig_account_id: str, sender_id: str, msg_id: str) -> None:
    """Menzione o post che cita @gatemilano nella storia → mettiamo un like ❤️
    invece di un messaggio di testo (più naturale e meno invadente)."""
    venue = _venue_for_account(ig_account_id)
    phone = f"ig:{sender_id[:12]}"
    context = {"ig_account_id": ig_account_id, "sender_id": sender_id}
    if is_human_takeover(phone):
        await notify_human_message(phone, venue, "[menzione storia]", context)
        return
    await react_to_message(ig_account_id, sender_id, msg_id, "love")
    await notify_conversation(phone, venue, "[menzione/post in storia]", "❤️ (reaction)", context)


async def process_ig_reaction(ig_account_id: str, sender_id: str, msg_id: str) -> None:
    """L'utente ha reagito a una nostra storia/messaggio → reagiamo a nostra volta
    con un ❤️, senza inviare testo."""
    venue = _venue_for_account(ig_account_id)
    phone = f"ig:{sender_id[:12]}"
    context = {"ig_account_id": ig_account_id, "sender_id": sender_id}
    if is_human_takeover(phone):
        await notify_human_message(phone, venue, "[reaction storia]", context)
        return
    await react_to_message(ig_account_id, sender_id, msg_id, "love")
    await notify_conversation(phone, venue, "[reaction su storia]", "❤️ (reaction)", context)
