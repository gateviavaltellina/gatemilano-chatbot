"""Shared RAG context builder for WhatsApp and Instagram webhooks."""
import logging
from datetime import datetime, timezone

from rag.event_store import (
    get_upcoming_events_compact,
    get_events_for_date,
    get_ticket_url_for_date,
    _store,
)
from rag.date_utils import extract_query_dates
from rag.vip_tables import get_vip_tables_context
from rag.knowledge_cache import get as get_static_knowledge

logger = logging.getLogger(__name__)

_VIP_TRIGGERS = {
    "tavolo", "tavoli", "vip", "bottle", "bottiglia", "minimo",
    "table", "tables", "backstage",
}
_OTHER_VENUE = {"gate_milano": "gate_sardinia", "gate_sardinia": "gate_milano"}
_OTHER_VENUE_NAME = {"gate_milano": "Gate Sardinia", "gate_sardinia": "Gate Milano"}


async def build_rag_context(venue: str, text: str) -> tuple[str, list[str]]:
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

    # 1. VIP context — only when VIP keywords are detected
    vip_context = ""
    if any(t in lower_text for t in _VIP_TRIGGERS):
        ticket_url = ""
        if query_dates:
            ticket_url = get_ticket_url_for_date(venue, query_dates[0])
        if not ticket_url:
            # Fallback: next upcoming event with an Xceed ticket
            now_ts = int(datetime.now(timezone.utc).timestamp())
            for e in sorted(_store.get(venue, []), key=lambda x: x["metadata"].get("date_ts", 0)):
                meta = e["metadata"]
                if (
                    meta.get("type") == "event"
                    and meta.get("date_ts", 0) >= now_ts
                    and "xceed" in meta.get("ticket_url", "")
                ):
                    ticket_url = meta["ticket_url"]
                    break
        if ticket_url:
            vip_context = await get_vip_tables_context(ticket_url)

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
