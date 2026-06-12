"""Shared RAG context builder for WhatsApp and Instagram webhooks."""
import logging

from rag.event_store import (
    get_upcoming_events_compact,
    get_events_for_date,
    get_vip_candidates,
)
from rag.date_utils import extract_query_dates
from rag.vip_tables import get_vip_tables_context, get_vip_tables_via_site

logger = logging.getLogger(__name__)

_VIP_TRIGGERS = {
    "tavolo", "tavoli", "vip", "bottle", "bottiglia", "bottiglie", "minimo",
    "table", "tables", "backstage", "disponibil", "prenotare", "prenot",
    "zona f", "zona b", "zona c", "balcony", "console",
}
_OTHER_VENUE = {"gate_milano": "gate_sardinia", "gate_sardinia": "gate_milano"}
_OTHER_VENUE_NAME = {"gate_milano": "Gate Sardinia", "gate_sardinia": "Gate Milano"}
# Slug canale Xceed per i link di checkout dei tavoli VIP (per venue).
_VENUE_CHANNEL = {"gate_milano": "gate-milano", "gate_sardinia": "gate-sardinia"}


async def build_rag_context(venue: str, text: str, history: list[dict] | None = None) -> tuple[str, list[str]]:
    """
    Build RAG context for a user message.
    Returns (rag_context_string, query_dates_list).

    Compact upcoming events (1 line each) keep context lean for tourists
    planning ahead; full event details are only injected for explicitly queried dates.
    """
    lower_text = text.lower()
    query_dates = extract_query_dates(text)
    other_venue = _OTHER_VENUE.get(venue, "gate_milano")
    other_venue_name = _OTHER_VENUE_NAME.get(venue, "Gate Milano")
    channel = _VENUE_CHANNEL.get(venue, "gate-milano")

    # Check history for VIP topic — last 6 messages (3 turns)
    history_text = " ".join(
        m.get("content", "") for m in (history or [])[-6:]
    ).lower()

    # 1. VIP context — when VIP keywords in current message OR recent history.
    # Candidati: eventi della data richiesta, oppure i prossimi in programma (in ordine);
    # ci si ferma al primo che ha tavoli. Milano usa l'endpoint del sito (single source
    # of truth, name+date); le altre venue restano sulla pipeline Xceed diretta (per ora).
    vip_context = ""
    if any(t in lower_text for t in _VIP_TRIGGERS) or any(t in history_text for t in _VIP_TRIGGERS):
        candidates = get_vip_candidates(venue, query_dates[0] if query_dates else None)
        for name, date_iso, ticket_url in candidates:
            if venue == "gate_milano":
                logger.debug("VIP lookup (sito) per %s %s", name, date_iso)
                result = await get_vip_tables_via_site(name, date_iso)
            else:
                if "xceed" not in (ticket_url or ""):
                    continue
                logger.debug("VIP lookup (xceed) per ticket_url=%s", ticket_url[:60])
                result = await get_vip_tables_context(ticket_url, channel)
            if result:
                vip_context = result
                break

    # 2. Full event details for specifically queried dates
    date_parts = []
    for date_str in query_dates:
        day_events = get_events_for_date(venue, date_str)
        if day_events:
            date_parts.append(day_events)
        other_events = get_events_for_date(other_venue, date_str)
        if other_events:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_events}")

    # 3. Compact upcoming list (title + date + link, 1 line per event, 14 giorni)
    upcoming = get_upcoming_events_compact(venue, days=14)

    # NB: la knowledge base statica NON è più qui — è costante per venue e viene
    # iniettata nel blocco system cacheato (vedi ai/claude_client.build_system_blocks).
    # Qui resta solo il contesto DINAMICO (cambia per messaggio/giorno).
    parts = [p for p in [vip_context, *date_parts, upcoming] if p]
    return "\n\n---\n\n".join(parts), query_dates
