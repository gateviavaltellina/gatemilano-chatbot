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
                await gm.send_draft(draft_id, label=gm.BOT_SENT_LABEL)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return "❌ Bozza non trovata in Gmail: forse è già stata inviata o cancellata da lì."
                raise
            await _mark_done(ctx, author)
            return f"✅ Inviata a {ctx['mail']['from_email']} (approvata da {author})."

        text = content[3:].strip()
        await gm.send_reply(ctx["mail"], text, label=gm.BOT_SENT_LABEL)
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


# --- rimborsi -----------------------------------------------------------------------

async def refund_action(content: str, ctx: dict, author: str) -> str | None:
    """Comandi sulle schede rimborso: !rimborsa, !fatto, !rifiuta [nota], !r <testo>."""
    from mail import refunds, templates, xceed_mcp
    from config import settings

    cmd = content.split(" ", 1)[0]
    if cmd not in ("!rimborsa", "!fatto", "!rifiuta", "!r", "!verifica", "!inoltra"):
        return None
    if cmd == "!r" and not content[3:].strip():
        return None
    if not gm.configured():
        return "❌ Gmail non configurato sul server."
    mail = ctx.get("mail") or {}
    if not mail.get("from_email"):
        return "❌ Dati di questa richiesta non più disponibili: rispondi da Gmail."
    if cmd == "!verifica":
        try:
            esito = await refunds.recheck(mail["from_email"])
        except Exception as e:
            return f"❌ Verifica non riuscita: {str(e)[:150]}"
        return f"🔎 Verifica rifatta ({esito}): trovi la scheda aggiornata qui sotto."
    if ctx.get("sent_by") and cmd != "!r":
        return f"⚠️ Richiesta già chiusa da {ctx['sent_by']}: nessuna nuova azione."
    lang = ctx.get("lingua", "it")

    async def _send(text: str) -> None:
        await gm.send_reply(mail, text, label=refunds.LABEL_BOT_SENT, reply_to=settings.refund_inbox)

    async def _close(label: str) -> None:
        ctx["sent_by"] = author
        for mid in ctx.get("gmail_ids", []):
            try:
                await gm.add_label(mid, label)
            except Exception:
                logger.debug("Etichetta %s non applicata", label, exc_info=True)

    try:
        if cmd == "!r":
            await _send(content[3:].strip())
            return f"✅ Risposta di {author} inviata a {mail['from_email']}."
        if cmd == "!inoltra":
            esito = await refunds.forward_by_staff(mail["from_email"], content[len("!inoltra"):].strip())
            ctx["sent_by"] = author
            return f"📨 {esito} ({author})."
        if cmd == "!rifiuta":
            await _send(templates.refund_refused(lang, content[len("!rifiuta"):].strip()))
            await _close(refunds.LABEL_REFUSED)
            return f"✅ Rifiuto inviato a {mail['from_email']} ({author})."
        if cmd == "!rimborsa":
            if not (xceed_mcp.configured() and settings.xceed_mcp_refund_tool):
                return ("ℹ️ Il rimborso automatico su Xceed non è ancora collegato. Rimborsa "
                        "dalla dashboard Xceed, poi scrivi `!fatto` qui: il cliente riceve la conferma.")
            if not ctx.get("booking_ids"):
                return "❌ Nessuna prenotazione Xceed associata: rimborsa da Xceed e poi `!fatto`."
            plan = ctx.get("refund_plan")
            if plan is None:
                # Scheda di prima dei rimborsi parziali: senza il piano si rischierebbe di
                # rimborsare anche chi è entrato. Le schede verdi erano tutte "non usati".
                if ctx.get("status") != "ok":
                    return ("⚠️ Scheda creata prima dei rimborsi parziali: fai `!verifica` e poi "
                            "`!rimborsa` sulla scheda aggiornata (rimborsa solo i non entrati).")
                plan = [{"booking_id": b, "full": True, "codes": [], "tickets": 0}
                        for b in ctx["booking_ids"]]
            if not plan:
                return ("❌ Nessun biglietto da rimborsare: risultano tutti usati all'ingresso o "
                        "già rimborsati. Se è un errore, rimborsa da Xceed e poi `!fatto`.")
            n = await refunds.execute_plan(plan, lang, ctx.setdefault("refunded_ids", []))
            partial = bool(ctx.get("refund_partial"))
            await _send(templates.refund_done(lang, ctx.get("evento", ""), tickets=n, partial=partial))
            await _close(refunds.LABEL_DONE)
            what = f"{n} biglietti non usati (parziale)" if partial else "rimborso totale"
            return f"✅ Rimborsato su Xceed — {what} — e cliente avvisato ({author})."
        # `!fatto`: rimborsato a mano dalla dashboard, il messaggio segue la scheda.
        await _send(templates.refund_done(
            lang, ctx.get("evento", ""), partial=bool(ctx.get("refund_partial")),
            tickets=sum(p.get("tickets", 0) for p in ctx.get("refund_plan") or [])))
        await _close(refunds.LABEL_DONE)
        return f"✅ Rimborso segnato come fatto e cliente avvisato ({author})."
    except Exception as e:
        logger.error("Comando rimborso %r fallito: %s", cmd, e)
        hint = ""
        if cmd == "!rimborsa":
            hint = ("\nSe la finestra Xceed è chiusa (dopo il lunedì mattina) usa `!inoltra`: "
                    "la richiesta va a Xceed e il cliente viene avvisato.")
        return f"❌ Non riuscito: {str(e)[:150]}{hint}"
