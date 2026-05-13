"""Shared RAG context builder for WhatsApp and Instagram webhooks."""
import logging

from rag.event_store import (
    get_upcoming_events_compact,
    get_events_for_date,
    get_ticket_url_for_date,
    get_all_ticket_urls_for_date,
    _store,
    _today_start_utc,
)
from rag.date_utils import extract_query_dates
from rag.vip_tables import get_vip_tables_context
from rag.knowledge_cache import get as get_static_knowledge

logger = logging.getLogger(__name__)

_VIP_TRIGGERS = {
    "tavolo", "tavoli", "vip", "bottle", "bottiglia", "bottiglie", "minimo",
    "table", "tables", "backstage", "disponibil", "prenotare", "prenot",
    "zona f", "zona b", "zona c", "balcony", "console",
}
_OTHER_VENUE = {"gate_milano": "gate_sardinia", "gate_sardinia": "gate_milano"}
_OTHER_VENUE_NAME = {"gate_milano": "Gate Sardinia", "gate_sardinia": "Gate Milano"}


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

    # Check history for VIP topic — last 6 messages (3 turns)
    history_text = " ".join(
        m.get("content", "") for m in (history or [])[-6:]
    ).lower()

    # 1. VIP context — when VIP keywords in current message OR recent history
    vip_context = ""
    if any(t in lower_text for t in _VIP_TRIGGERS) or any(t in history_text for t in _VIP_TRIGGERS):
        if query_dates:
            # User asked about a specific date — try all events on that date until one has VIP tables
            # (multiple events on same day e.g. THE URS CONCERT + PERREO XL → try each in order)
            for ticket_url in get_all_ticket_urls_for_date(venue, query_dates[0]):
                logger.debug("VIP lookup triggered for ticket_url=%s", ticket_url[:60])
                result = await get_vip_tables_context(ticket_url)
                if result:
                    vip_context = result
                    break
        else:
            # No specific date — try all upcoming Xceed events until one returns VIP tables
            # Use today_start_utc (midnight Rome) not now_ts — date_ts is stored as midnight UTC
            today_ts = _today_start_utc()
            candidates = [
                e["metadata"]["ticket_url"]
                for e in sorted(_store.get(venue, []), key=lambda x: x["metadata"].get("date_ts", 0))
                if (
                    e["metadata"].get("type") == "event"
                    and e["metadata"].get("date_ts", 0) >= today_ts
                    and "xceed" in e["metadata"].get("ticket_url", "")
                )
            ]
            for url in candidates:
                logger.debug("VIP lookup trying ticket_url=%s", url[:60])
                result = await get_vip_tables_context(url)
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

    # 4. Static knowledge base
    static_knowledge = get_static_knowledge(venue)

    parts = [p for p in [vip_context, *date_parts, upcoming, static_knowledge] if p]
    return "\n\n---\n\n".join(parts), query_dates
