"""Agent di coordinamento staff in un gruppo WhatsApp (Cloud API Groups).

I messaggi di gruppo arrivano sullo STESSO webhook dei DM, con in più un campo
`group_id` sul messaggio (vedi reference Groups Messaging). Qui NON si risponde a
tutto (sarebbe spam in un gruppo): solo ai comandi con prefisso. L'invio usa
`recipient_type=group` + `to=<GROUP_ID>` (whatsapp.client.send_group_message).

Comandi:
  !help              — elenco comandi
  !eventi            — prossimi eventi (14 giorni)
  !stasera           — eventi del giorno di servizio
  !lineup <evento>   — lineup/dettagli di un evento
  !gate <domanda>    — domanda libera (Claude, con contesto eventi)

Sicurezza: risponde solo nei gruppi in allowlist (WA_GROUP_ALLOWLIST), se impostata.
"""
import logging

from config import settings
from whatsapp.client import send_group_message, mark_as_read
from rag.event_store import get_upcoming_events_compact, get_events_for_date, _store
from rag.date_utils import business_now
from rag.context_builder import build_rag_context
from ai.claude_client import generate_response

logger = logging.getLogger(__name__)

# Per ora un solo venue staff (Milano). Estendibile a un mapping gruppo→venue.
_VENUE = "gate_milano"

_PREFIXES = ("!gate", "!eventi", "!stasera", "!lineup", "!help")

_HELP = (
    "Comandi staff:\n"
    "• !eventi — prossimi eventi (14 giorni)\n"
    "• !stasera — eventi di stasera\n"
    "• !lineup <evento> — lineup di un evento\n"
    "• !gate <domanda> — chiedi qualsiasi cosa su eventi/biglietti/info"
)


def _allowlist() -> set:
    raw = settings.wa_group_allowlist or ""
    return {g.strip() for g in raw.split(",") if g.strip()}


async def process_group_message(group_id: str, sender: str, msg_id: str, text: str) -> None:
    allow = _allowlist()
    if group_id not in allow:
        # Default chiuso: l'agent risponde solo nei gruppi esplicitamente abilitati.
        # Loggo il group_id (intero) così puoi copiarlo in WA_GROUP_ALLOWLIST.
        logger.info("Messaggio gruppo IGNORATO (group_id non in allowlist): %s", group_id)
        return
    t = (text or "").strip()
    if not t.lower().startswith(_PREFIXES):
        return  # niente trigger → ignora (no spam)
    await mark_as_read(msg_id)
    reply = await _handle_command(t)
    if reply:
        await send_group_message(group_id, reply)


async def _handle_command(t: str) -> str:
    low = t.lower()
    if low.startswith("!help"):
        return _HELP
    if low.startswith("!eventi"):
        return get_upcoming_events_compact(_VENUE, days=14) or "Nessun evento nei prossimi 14 giorni."
    if low.startswith("!stasera"):
        today = business_now().strftime("%Y-%m-%d")
        return get_events_for_date(_VENUE, today) or "Nessun evento in programma stasera."
    if low.startswith("!lineup"):
        q = t[len("!lineup"):].strip()
        return _lineup_for(q) if q else "Uso: !lineup <nome evento>"
    if low.startswith("!gate"):
        q = t[len("!gate"):].strip()
        if not q:
            return "Uso: !gate <domanda>"
        rag_context, _ = await build_rag_context(_VENUE, q)
        return await generate_response(venue=_VENUE, user_message=q, rag_context=rag_context, history=[])
    return ""


def _lineup_for(query: str) -> str:
    ql = query.lower()
    for e in _store.get(_VENUE, []):
        meta = e["metadata"]
        if meta.get("type") != "event":
            continue
        if ql in (meta.get("event_name", "") or "").lower():
            doc = e["document"]
            keep = [l for l in doc.split("\n")
                    if l.startswith(("EVENTO:", "Data:", "Sala:", "Artisti:", "Generi:"))]
            return "\n".join(keep) or doc[:400]
    return f"Non trovo un evento che corrisponde a '{query}'."
