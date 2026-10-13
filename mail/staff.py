"""Comandi dello staff su Discord per le email: come `!r` per Instagram e WhatsApp.

Si usano in reply al messaggio Discord dell'email:
- `!ok`        invia la bozza proposta dal bot (anche se modificata in Gmail);
- `!r <testo>` invia un testo scritto dallo staff, e scarta la bozza.

Sono azioni umane: valgono anche con `MAIL_DRAFT_ONLY` attivo, che riguarda solo
l'invio AUTOMATICO.
"""
from __future__ import annotations

import logging

import httpx

from mail import gmail_client as gm

logger = logging.getLogger(__name__)


async def _already_answered(ctx: dict) -> str:
    """Motivo per non inviare, o "" se si può."""
    if ctx.get("sent_by"):
        who = "il bot" if ctx["sent_by"] == "bot" else ctx["sent_by"]
        return f"⚠️ Già risposto ({who}): niente doppio invio. Per scrivere ancora usa Gmail."
    thread_id = (ctx.get("mail") or {}).get("thread_id")
    if thread_id and await gm.thread_has_our_reply(thread_id):
        return "⚠️ In questa conversazione avete già risposto (da Gmail?): non invio."
    return ""


async def _mark_done(ctx: dict, author: str) -> None:
    from mail.responder import LABEL_DONE
    ctx["sent_by"] = author
    if ctx.get("gmail_id"):
        try:
            await gm.add_label(ctx["gmail_id"], LABEL_DONE)
        except Exception:
            logger.debug("Etichetta Risposto non applicata", exc_info=True)


async def staff_action(content: str, ctx: dict, author: str) -> str | None:
    """Esegue il comando e ritorna il testo di conferma, o None se non è un comando email."""
    is_ok = content == "!ok"
    is_r = content.startswith("!r ") and content[3:].strip()
    if not (is_ok or is_r):
        return None
    if not gm.configured():
        return "❌ Gmail non configurato sul server."
    if not (ctx.get("mail") or {}).get("from_email"):
        return "❌ Dati di questa email non più disponibili: rispondi da Gmail."
    try:
        blocked = await _already_answered(ctx)
        if blocked:
            return blocked

        if is_ok:
            draft_id = ctx.get("draft_id")
            if not draft_id:
                return "❌ Il bot non ha proposto una risposta: usa `!r <testo>`."
            try:
                await gm.send_draft(draft_id)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return "❌ Bozza non trovata in Gmail: forse è già stata inviata o cancellata da lì."
                raise
            await _mark_done(ctx, author)
            return f"✅ Inviata a {ctx['mail']['from_email']} (approvata da {author})."

        text = content[3:].strip()
        await gm.send_reply(ctx["mail"], text)
        if ctx.get("draft_id"):
            try:
                await gm.delete_draft(ctx["draft_id"])
            except Exception:
                logger.debug("Bozza non cancellata (già inviata o rimossa?)", exc_info=True)
        await _mark_done(ctx, author)
        return f"✅ Risposta di {author} inviata a {ctx['mail']['from_email']}."
    except Exception as e:
        logger.error("Comando email %r fallito: %s", content[:20], e)
        return f"❌ Invio non riuscito: {str(e)[:150]}"
