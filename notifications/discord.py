from __future__ import annotations

import httpx
import logging
import re
from config import settings

logger = logging.getLogger(__name__)

VENUE_EMOJI = {
    "gate_milano": "🏙️",
    "gate_sardinia": "🏖️",
}


def _mask_phone(phone: str) -> str:
    if len(phone) > 6:
        return phone[:4] + "****" + phone[-3:]
    return "****"


def _webhook_url_for(phone: str) -> str:
    is_ig = phone.startswith("ig:")
    if is_ig and settings.discord_ig_webhook_url:
        return settings.discord_ig_webhook_url.split("?")[0] + "?wait=true"
    return settings.discord_webhook_url.split("?")[0] + "?wait=true"


def _conversation_context(context: dict | None, venue: str, user_msg: str, bot_reply: str) -> dict:
    """Context registrato per il messaggio Discord: include venue + esempio, così
    una reply !regola può catturare domanda e risposta sbagliata da correggere.
    user_msg/bot_reply sono troncati a 1024 (come l'embed) per non gonfiare la
    persistenza con messaggi lunghi."""
    return {
        **(context or {}),
        "venue": venue,
        "user_msg": (user_msg or "")[:1024],
        "bot_reply": (bot_reply or "")[:1024],
    }


async def notify_conversation(phone: str, venue: str, user_msg: str, bot_reply: str,
                              context: dict = None, delivered: bool = True) -> None:
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    url = _webhook_url_for(phone)
    if not url or url.startswith("?"):
        return
    is_ig = phone.startswith("ig:")
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    source = "📸 IG" if is_ig else "💬 WA"
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    # delivered=False: l'API (IG/WA) ha RIFIUTATO l'invio — il cliente non ha
    # ricevuto la risposta. Senza allarme, su Discord sembrerebbe tutto ok e il
    # cliente resterebbe nel vuoto (caso reale: risposta manuale arrivata 5 ore dopo).
    bot_field = "🤖 Bot" if delivered else "🤖 Bot — ⚠️ NON CONSEGNATO"
    payload = {
        "embeds": [
            {
                "color": (0xE1306C if is_ig else 0x7C3AED) if delivered else 0xDC2626,
                "description": f"{emoji} {venue_label} · {source} · {masked}"
                + ("" if delivered else " — ⚠️ INVIO FALLITO"),
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": bot_field, "value": bot_reply[:1024] or "​", "inline": False},
                ],
            }
        ]
    }
    if not delivered:
        payload["content"] = (
            "⚠️ **INVIO FALLITO** — il cliente NON ha ricevuto questa risposta. "
            "Rispondi tu con `!r <testo>` in reply a questo messaggio."
        )
    # ?wait=true → Discord restituisce il messaggio con l'ID (necessario per human takeover)
    await _post_and_register(url, payload, phone, _conversation_context(context, venue, user_msg, bot_reply))


async def _post_and_register(url: str, payload: dict, phone: str, context: dict | None,
                             attempts: int = 2) -> None:
    """POST a Discord con retry: le notifiche (soprattutto gli allarmi INVIO
    FALLITO) sono l'ultima rete di sicurezza — un singhiozzo di Discord non deve
    farle sparire. Registra l'ID messaggio per il takeover (!t / !r in reply)."""
    import asyncio
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(1, attempts + 1):
            try:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                msg_id = r.json().get("id")
                if msg_id:
                    from notifications.discord_bot import register_message
                    register_message(msg_id, phone, context)
                return
            except Exception as e:
                if i < attempts:
                    await asyncio.sleep(2)
                    continue
                logger.warning("Discord notify failed (%d tentativi): %s", attempts, e)


async def notify_human_message(phone: str, venue: str, user_msg: str, context: dict = None) -> None:
    """Notifica Discord quando il bot è in pausa (human takeover)."""
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    payload = {
        "embeds": [
            {
                "color": 0xF59E0B,
                "description": f"{emoji} {venue_label} · {masked} — ⏸️ STAFF MODE",
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": "ℹ️ Azioni", "value": "Rispondi con `!r <testo>` oppure `!rel` per riattivare il bot.", "inline": False},
                ],
            }
        ]
    }
    url = _webhook_url_for(phone)
    await _post_and_register(url, payload, phone, context)


async def notify_escalation(
    phone: str, venue: str, user_msg: str, categories: list, context: dict = None
) -> None:
    """Alert prominente allo staff quando un messaggio tocca un tema sensibile
    (accessibilità, rimborsi, salute, reclami). Il bot risponde comunque; questo
    serve a far intervenire un umano in fretta. Registra l'ID così lo staff può
    prendere in carico con !t / !r direttamente in reply."""
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    is_ig = phone.startswith("ig:")
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    source = "📸 IG" if is_ig else "💬 WA"
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    cats = " · ".join(categories) if categories else "tema sensibile"
    payload = {
        "content": "🚨 **ATTENZIONE STAFF** — messaggio sensibile, valuta presa in carico",
        "embeds": [
            {
                "color": 0xDC2626,
                "description": f"🚨 {cats}\n{emoji} {venue_label} · {source} · {masked}",
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": "ℹ️ Azioni", "value": "`!t` per prendere in carico · `!r <testo>` per rispondere a mano.", "inline": False},
                ],
            }
        ],
    }
    url = _webhook_url_for(phone)
    await _post_and_register(url, payload, phone, context)


async def notify_group_event(group_id: str, sender: str, user_msg: str, bot_reply: str = None, enabled: bool = True) -> None:
    """Pubblica su Discord l'attività dell'agent di gruppo WhatsApp (canale WA).

    - enabled=False: gruppo NON ancora in allowlist → mostra il group_id INTERO in
      un blocco di codice, così lo copi in WA_GROUP_ALLOWLIST (niente caccia ai log).
    - enabled=True: mostra comando staff + risposta del bot.
    """
    masked = _mask_phone(sender)
    if enabled:
        color = 0x16A34A
        description = f"👥 Gruppo staff · WA · {masked}"
        fields = [
            {"name": "💬 Comando", "value": user_msg[:1024] or "​", "inline": False},
            {"name": "🤖 Bot", "value": (bot_reply or "")[:1024] or "​", "inline": False},
        ]
    else:
        color = 0x9CA3AF
        description = "👥 Nuovo gruppo WhatsApp NON abilitato — copia il group_id in `WA_GROUP_ALLOWLIST`"
        fields = [
            {"name": "group_id", "value": f"```{group_id}```", "inline": False},
            {"name": "💬 Messaggio", "value": user_msg[:300] or "​", "inline": False},
        ]

    payload = {"embeds": [{"color": color, "description": description, "fields": fields}]}

    # 1) Webhook dedicato del canale gruppo (preferito, più robusto)
    if settings.discord_group_webhook_url:
        if await _post_webhook(settings.discord_group_webhook_url, payload):
            return
    # 2) Canale dedicato via bot (per channel id)
    if settings.discord_group_channel_id:
        from notifications.discord_bot import post_embed_to_channel
        if await post_embed_to_channel(settings.discord_group_channel_id, description, fields, color):
            return
    # 3) Fallback: webhook del canale WA
    if settings.discord_webhook_url:
        await _post_webhook(settings.discord_webhook_url, payload)


async def _post_webhook(url: str, payload: dict) -> bool:
    url = url.split("?")[0] + "?wait=true"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Discord webhook post failed: %s", e)
            return False


# --- email (info@) -------------------------------------------------------------

_MAIL_STATUS = {
    # stato: (colore, intestazione risposta, riga azioni)
    "bozza": (0xF59E0B, "🤖 Bot — bozza da approvare",
              "`!ok` invia questa risposta · `!r <testo>` invia un testo tuo · "
              "`!regola …` correggi il bot. Puoi anche modificare la bozza in Gmail "
              "e poi dare `!ok`: parte la versione modificata."),
    "inviata": (0x16A34A, "🤖 Bot — ✅ inviata in automatico",
                "`!regola …` se la risposta non andava bene."),
    "revisione": (0xDC2626, "🤖 Bot — ⚠️ nessuna risposta proposta",
                  "`!r <testo>` per rispondere da qui, oppure rispondi da Gmail."),
}


def _mail_key(msg: dict) -> str:
    return f"mail:{msg.get('thread_id') or msg.get('id')}"


def _cut(text: str, limit: int = 1024, tail: str = "…") -> str:
    # Le email HTML convertite in testo hanno spesso decine di righe vuote: su
    # Discord diventano un muro bianco (caso reale SYN*K 13/10).
    text = re.sub(r"[ \t\u00a0\u200b\u034f]+\n", "\n", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - len(tail)] + tail


async def notify_email(msg: dict, verdict: dict, venue: str, reply: str, status: str,
                       reason: str = "", draft_id: str = "") -> None:
    """Email su Discord come le chat IG/WA: cosa ha scritto il cliente, cosa propone
    il bot, e i comandi per inviarla senza aprire Gmail (in reply a questo messaggio).
    """
    url = settings.discord_mail_webhook_url or settings.discord_webhook_url
    if not url:
        return
    color, reply_header, actions = _MAIL_STATUS.get(status, _MAIL_STATUS["revisione"])
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue, venue)
    header = (f"📧 Email · {VENUE_EMOJI.get(venue, '')} {venue_label} · "
              f"{msg.get('from_email', '?')} · {verdict.get('categoria', '?')}")
    fields = [
        {"name": "✉️ Oggetto", "value": _cut(msg.get("subject") or "(senza oggetto)", 256),
         "inline": False},
        {"name": "👤 Cliente", "value": _cut(msg.get("body")) or "​", "inline": False},
        {"name": reply_header,
         "value": _cut(reply, tail="… (continua nella bozza in Gmail)") or "—", "inline": False},
    ]
    if reason:
        fields.append({"name": "ℹ️ Perché non è partita da sola", "value": _cut(reason, 256),
                       "inline": False})
    fields.append({"name": "⌨️ Comandi (in reply)", "value": actions, "inline": False})
    payload = {"embeds": [{"color": color, "description": header, "fields": fields}]}
    context = {
        "channel": "email",
        "venue": venue,
        "user_msg": _cut(msg.get("body")),
        "bot_reply": _cut(reply),
        "draft_id": draft_id,
        "gmail_id": msg.get("id", ""),
        "sent_by": "bot" if status == "inviata" else "",
        # Quanto serve per rispondere nel thread giusto con !r.
        "mail": {k: msg.get(k, "") for k in
                 ("from_email", "subject", "message_id_header", "references", "thread_id")},
    }
    await _post_and_register(url.split("?")[0] + "?wait=true", payload, _mail_key(msg), context)


# --- rimborsi (rimborsi@) ---------------------------------------------------------

_REFUND_STATUS = {
    # stato: (colore, titolo, comandi)
    "ok": (0x16A34A, "✅ Requisiti OK — da rimborsare",
           "`!rimborsa` rimborsa su Xceed · `!fatto` se l'hai già rimborsato da Xceed · "
           "`!rifiuta <nota>` · `!r <testo>` · `!verifica` rifà i controlli"),
    "da_verificare": (0xF59E0B, "⚠️ Richiesta completa, ma qualcosa non torna",
                      "`!rimborsa` · `!fatto` · `!rifiuta <nota>` · `!r <testo>` · "
                      "`!verifica` rifà i controlli"),
    "incompleta": (0x64748B, "📎 Incompleta — al cliente è stato scritto cosa manca",
                   "Aspetta la sua risposta, oppure `!r <testo>`."),
    "non_rimborsabile": (0xDC2626, "❌ Fuori policy — non è venuto per motivi propri",
                         "`!rifiuta` invia la risposta standard · `!r <testo>`"),
    "nota": (0x64748B, "ℹ️ Aggiornamento", "`!r <testo>` se serve rispondere."),
    "errore": (0xDC2626, "⚠️ Da guardare a mano",
               "`!verifica` riprova · `!r <testo>` · `!rifiuta` · `!fatto`"),
}

_MISSING_LABEL = {"prova_acquisto": "prova d'acquisto", "biglietti": "biglietti",
                  "motivazione": "motivazione del respingimento"}


async def notify_refund(sender: str, msgs: list[dict], analysis: dict, verdict: dict,
                        found: dict, note: str = "") -> None:
    url = (settings.discord_refund_webhook_url or settings.discord_mail_webhook_url
           or settings.discord_webhook_url)
    if not url:
        return
    status = verdict.get("status", "errore")
    color, title, commands = _REFUND_STATUS.get(status, _REFUND_STATUS["errore"])
    fields = []
    if note:
        fields.append({"name": "ℹ️ Nota", "value": _cut(note, 512), "inline": False})
    text = "\n\n".join(_cut(m.get("body") or "(solo allegati)", 400) for m in msgs[-3:])
    n_att = sum(len(m.get("attachments", [])) for m in msgs)
    fields.append({"name": f"👤 Cliente · {len(msgs)} email · {n_att} allegati",
                   "value": _cut(text) or "​", "inline": False})
    if verdict.get("missing"):
        fields.append({"name": "📎 Mancano",
                       "value": ", ".join(_MISSING_LABEL.get(m, m) for m in verdict["missing"]),
                       "inline": False})
    if verdict.get("checks"):
        fields.append({"name": "🔎 Verifica",
                       "value": _cut("\n".join(f"{i} {t}" for i, t in verdict["checks"])),
                       "inline": False})
    fields.append({"name": "⌨️ Comandi (in reply)", "value": commands, "inline": False})
    payload = {"embeds": [{"color": color, "description": f"💸 Rimborso · {sender} — {title}",
                           "fields": fields}]}
    latest = msgs[-1] if msgs else {}
    summary = xt_summary(found)
    context = {
        "channel": "refund",
        "venue": "gate_milano",
        "status": status,
        "lingua": analysis.get("lingua", "it"),
        "evento": (found.get("event") or {}).get("name", "") or analysis.get("evento", ""),
        "booking_ids": summary.get("booking_ids", []),
        "gmail_ids": [m.get("id") for m in msgs if m.get("id")],
        "user_msg": _cut(text),
        "bot_reply": "",
        "sent_by": "",
        "mail": {k: latest.get(k, "") for k in
                 ("from_email", "subject", "message_id_header", "references", "thread_id")},
    }
    await _post_and_register(url.split("?")[0] + "?wait=true", payload, f"refund:{sender}", context)


def xt_summary(found: dict) -> dict:
    if not found or not found.get("tickets"):
        return {}
    from mail.xceed_tickets import summarize
    return summarize(found["tickets"])
