import json
import logging
import random
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, HTTPException, BackgroundTasks

from config import settings
from webhook_security import verify_meta_signature
from rag.context_builder import build_rag_context
from ai.claude_client import generate_response, last_api_error, API_ERROR_FALLBACK_PREFIX, fetch_image_block


def _relay_with_api_error(reply: str, full_reply: str) -> str:
    """Se la risposta è il fallback d'errore del modello, allega allo STAFF (relay
    Discord) il motivo reale dell'errore API — così un guasto sistematico (credito
    esaurito / modello inesistente / 401) è diagnosticabile, senza mostrarlo al cliente."""
    if reply.startswith(API_ERROR_FALLBACK_PREFIX):
        err = last_api_error() or "sconosciuto"
        return f"{full_reply}\n\n[⚠️ ERRORE API modello (non mostrato al cliente): {err}]"
    return full_reply
from notifications.discord import notify_conversation, notify_human_message, notify_escalation
from notifications.discord_bot import is_human_takeover
from notifications.escalation import detect_sensitive
from notifications.drinklist import (
    should_send_drinklist, drinklist_link_message,
    should_send_drink_menu, drink_menu_link_message,
    should_send_food_menu, food_menu_link_message,
)
from notifications.debug_trace import record as _trace
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
                # Risposta a una nostra STORIA: IG manda reply_to.story con l'URL del
                # media. Passiamo l'URL così il bot può VEDERE la storia (evento?
                # assunzioni? promo?) e capire la domanda; se l'immagine non si può
                # scaricare (storia video, errore), resta il flag per l'hint testuale.
                story = (msg.get("reply_to") or {}).get("story") or {}
                is_story_reply = bool(story)
                story_image_url = story.get("url") or None
                if text:
                    _trace("ig", sender_id, text, "webhook in ingresso", account=ig_account_id)
                    background_tasks.add_task(
                        process_ig_message, ig_account_id, sender_id, text,
                        is_story_reply, story_image_url,
                    )
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


async def process_ig_message(ig_account_id: str, sender_id: str, text: str,
                             is_story_reply: bool = False, story_image_url: str | None = None) -> None:
    try:
        await _process_ig_message(ig_account_id, sender_id, text, is_story_reply, story_image_url)
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


_STORY_REPLY_HINT = (
    "NOTA CONTESTO: l'utente sta rispondendo a una NOSTRA STORIA Instagram, ma tu NON "
    "vedi il contenuto della storia. Quindi una domanda generica (es. 'da che età?', "
    "'stasera?', 'quanto costa?', 'a che ora?', 'come funziona?') NON è per forza "
    "sull'ingresso al locale: potrebbe riferirsi a ciò che c'è nella storia — un evento, "
    "un annuncio di LAVORO/assunzioni, una promo, un giveaway. NON assumere che sia "
    "sull'ingresso: se il riferimento è ambiguo, CHIEDI gentilmente a cosa si riferisce / "
    "cosa diceva la storia, così rispondi giusto. In particolare 'da che età' su una "
    "storia di assunzioni riguarda l'età per LAVORARE (18+), non l'ingresso (16+)."
)


async def _process_ig_message(ig_account_id: str, sender_id: str, text: str,
                              is_story_reply: bool = False, story_image_url: str | None = None) -> None:
    venue = _venue_for_account(ig_account_id)
    conv = _get_conversation(ig_account_id, sender_id)
    phone = f"ig:{sender_id[:12]}"
    context = {"ig_account_id": ig_account_id, "sender_id": sender_id}
    _trace("ig", sender_id, text, "ricevuto", venue=venue, account=ig_account_id)

    if is_human_takeover(phone):
        _trace("ig", sender_id, text, "takeover (bot in pausa)")
        await notify_human_message(phone, venue, text, context)
        return

    rag_context, _ = await build_rag_context(venue, text, history=conv.get("history", []))
    # Risposta a una storia: prova a scaricare l'immagine così il modello la VEDE. Se
    # riesce, niente hint testuale (ce l'ha davanti); se no (storia video/errore) e
    # comunque è una story reply, aggiungi l'hint testuale come ripiego.
    image_block = await fetch_image_block(story_image_url) if story_image_url else None
    if is_story_reply and image_block is None:
        rag_context = f"{_STORY_REPLY_HINT}\n\n---\n\n{rag_context}"

    _add_to_history(conv, "user", text)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],
        image_block=image_block,
    )
    _add_to_history(conv, "assistant", reply)

    # IG non allega PDF → i link (drinklist bottiglie / carta drink) vanno come testo.
    # Li ACCODIAMO alla risposta in un UNICO messaggio: così non capita che il bot dica
    # "ti mando il link" e poi il messaggio separato non parta. split_for_ig spezza da
    # sé se supera il limite. Il flag drinklist si alza solo a invio riuscito.
    low_t, low_r = text.lower(), reply.lower()
    want_bottle = should_send_drinklist(venue, low_t, low_r, conv.get("drinklist_sent", False))
    want_menu = should_send_drink_menu(venue, low_t, low_r)
    want_food = should_send_food_menu(venue, low_t, low_r)
    links = []
    if want_bottle and (lm := drinklist_link_message(venue)):
        links.append(lm)
    if want_menu and (mm := drink_menu_link_message(venue)):
        links.append(mm)
    if want_food and (fm := food_menu_link_message(venue)):
        links.append(fm)
    full_reply = reply if not links else reply.rstrip() + "\n\n" + "\n\n".join(links)

    sent = await send_ig_message(ig_account_id, sender_id, full_reply)
    _trace("ig", sender_id, full_reply, "risposta", inviata=("SI" if sent else "NO"))
    if sent and want_bottle:
        conv["drinklist_sent"] = True

    sensitive = detect_sensitive(text)
    if sensitive:
        await notify_escalation(phone, venue, text, sensitive, context)

    # Relay Discord: mostra ciò che il cliente ha DAVVERO ricevuto (link drinklist/carta
    # drink accodati inclusi), non la sola risposta LLM — altrimenti lo staff crede che il
    # link non sia partito quando invece è nel messaggio inviato.
    relay_reply = _relay_with_api_error(reply, full_reply)
    await notify_conversation(phone, venue, text, relay_reply, context, delivered=sent)


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
