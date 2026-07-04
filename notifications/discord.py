from __future__ import annotations

import httpx
import logging
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
