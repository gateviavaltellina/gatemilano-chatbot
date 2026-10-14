"""Segue le risposte dei clienti alle mail del bot.

Il bot risponde da george@ (alias "Invia come"), quindi i clienti rispondono lì e
non a info@ o rimborsi@ (caso reale 13/10: Aymen ha risposto a george@ e il bot non
l'ha visto). Qui si guardano le mail arrivate a george@ e si prendono SOLO quelle
nelle conversazioni dove ha scritto il bot (messaggi con l'etichetta Bot/Inviato):
le conversazioni normali di George restano sue.

- conversazione di rimborso → la risposta rientra nella verifica (refunds);
- conversazione di info@ → bozza su Discord che tiene conto di quanto già scritto;
  non parte mai da sola.
Se dopo il messaggio del cliente lo staff ha già risposto a mano, il bot non tocca nulla.
"""
from __future__ import annotations

import logging
import time

from config import settings
from mail import gmail_client as gm
from mail import triage

logger = logging.getLogger(__name__)

# Thread già visti e risultati NON del bot: non si ricontrollano per un'ora
# (George riceve molta posta su george@ che non riguarda il bot).
_not_bot: dict[str, float] = {}


def _query() -> str:
    from mail import refunds, responder
    labels = (refunds.LABEL_SEEN, responder.LABEL_DONE, responder.LABEL_REVIEW,
              responder.LABEL_SKIPPED)
    return (f"to:{settings.mail_reply_as} newer_than:2d -in:sent "
            + " ".join(f'-label:"{lab}"' for lab in labels))


async def handle(message_id: str) -> str:
    from mail import refunds, responder
    msg = await gm.get_message(message_id)
    tid = msg["thread_id"]
    if _not_bot.get(tid, 0) > time.time():
        return "non del bot"
    thread = await gm.thread_overview(tid)
    bot_id = await gm.ensure_label(gm.BOT_SENT_LABEL)
    bot_msgs = [m for m in thread if bot_id in m["label_ids"]]
    if not bot_msgs:
        _not_bot[tid] = time.time() + 3600
        return "non del bot"
    when = msg.get("date_ms", 0)
    # Lo staff ha già risposto a mano dopo questo messaggio: è suo.
    if any("SENT" in m["label_ids"] and bot_id not in m["label_ids"] and m["date_ms"] > when
           for m in thread):
        _not_bot[tid] = time.time() + 3600
        return "seguita dallo staff"
    if triage.hard_skip(msg, box=settings.mail_reply_as):
        await gm.add_label(message_id, responder.LABEL_SKIPPED)
        return "ignorata"

    seen_id = await gm.ensure_label(refunds.LABEL_SEEN)
    if any(seen_id in m["label_ids"] for m in thread):
        if not settings.refunds_enabled:
            return "rimborsi spenti"
        return "rimborso: " + await refunds.process_sender(msg["from_email"], [msg])

    if not settings.mail_autoresponder_enabled:
        return "risponditore spento"
    last_bot = max(bot_msgs, key=lambda m: m["date_ms"])
    try:
        prior = (await gm.get_message(last_bot["id"])).get("body", "")
    except Exception:
        prior = ""
    return "info: " + await responder.process_one(message_id, followup=True, prior=prior)


async def run_once() -> dict:
    if not gm.configured() or not (settings.mail_autoresponder_enabled or settings.refunds_enabled):
        return {"skipped": "spento"}
    try:
        ids = await gm.list_unprocessed(_query(), 30)
    except Exception as e:
        logger.error("Lettura risposte a %s fallita: %s", settings.mail_reply_as, e)
        return {"error": str(e)[:200]}
    esiti: dict[str, int] = {}
    for mid in ids:
        try:
            esito = await handle(mid)
        except Exception as e:
            logger.exception("Seguito %s non gestito: %s", mid, e)
            esito = "errore"
        key = esito.split(" (")[0]
        esiti[key] = esiti.get(key, 0) + 1
    seguiti = {k: v for k, v in esiti.items() if k != "non del bot"}
    if seguiti:
        logger.info("Risposte ai messaggi del bot: %s", seguiti)
    return {"messaggi": len(ids), "esiti": esiti}
