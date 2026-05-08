"""
Simple in-memory event store. No embedding, no vector DB.
Populated on startup by Sanity/Xceed sync, reset on each restart.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# venue_key → list of {"id": str, "document": str, "metadata": dict}
_store: dict[str, list[dict]] = {}


def _get(venue: str) -> list[dict]:
    return _store.setdefault(venue, [])


def upsert_event(venue: str, event_id: str, document: str, metadata: dict):
    events = _get(venue)
    _store[venue] = [e for e in events if e["id"] != event_id]
    _store[venue].append({"id": event_id, "document": document, "metadata": metadata})


def delete_stale_events(venue: str, current_event_ids: list[str], source: str = None):
    current = set(current_event_ids)
    events = _get(venue)
    before = len(events)
    _store[venue] = [
        e for e in events
        if not (
            e["metadata"].get("type") == "event"
            and (source is None or e["metadata"].get("source") == source)
            and e["id"] not in current
        )
    ]
    removed = before - len(_store[venue])
    if removed:
        logger.info("Rimossi %d eventi stale da '%s'%s", removed, venue, f" ({source})" if source else "")


def get_upcoming_events(venue: str, days: int = 14) -> str:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    end_ts = now_ts + days * 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and now_ts <= e["metadata"].get("date_ts", 0) <= end_ts
    ]
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    return "\n\n---\n\n".join(e["document"] for e in events)


def get_upcoming_events_compact(venue: str, days: int = 14) -> str:
    """1-line-per-event summary — lighter RAG context for upcoming events.
    Full details are injected separately only for dates the user explicitly asked about."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    end_ts = now_ts + days * 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and now_ts <= e["metadata"].get("date_ts", 0) <= end_ts
    ]
    if not events:
        return ""
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    venue_label = venue.replace("_", " ").title()
    lines = [f"PROSSIMI EVENTI {venue_label.upper()} (prossimi {days} giorni):"]
    for e in events:
        meta = e["metadata"]
        name = meta.get("event_name", "Evento")
        date_line = ""
        for line in e["document"].split("\n"):
            if line.startswith("Data:"):
                date_line = line.replace("Data:", "").strip()
                break
        ticket = meta.get("ticket_url", "")
        ticket_str = f" — {ticket}" if ticket else ""
        lines.append(f"• {date_line}: {name}{ticket_str}")
    return "\n".join(lines)


def get_events_for_date(venue: str, date_str: str) -> str:
    day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and day_start <= e["metadata"].get("date_ts", 0) < day_end
    ]
    return "\n\n---\n\n".join(e["document"] for e in events)


def count(venue: str) -> int:
    return len([e for e in _get(venue) if e["metadata"].get("type") == "event"])


def get_ticket_url_for_date(venue: str, date_str: str) -> str:
    """Return the ticketUrl for the first event on date_str, or empty string."""
    day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400
    for e in _get(venue):
        meta = e["metadata"]
        if (meta.get("type") == "event"
                and day_start <= meta.get("date_ts", 0) < day_end
                and meta.get("ticket_url")):
            return meta["ticket_url"]
    return ""
