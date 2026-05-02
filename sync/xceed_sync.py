import httpx
import logging
import time
from datetime import datetime, timezone
from config import settings
from rag.chromadb_manager import chromadb_manager

logger = logging.getLogger(__name__)

PARTNER_BASE = "https://partner.xceed.me"
EVENTS_BASE = "https://events.xceed.me"
OFFER_BASE = "https://offer.xceed.me"

# Venue names as they appear in Xceed events (from xceed_client.py context)
XCEED_VENUE_FILTER = {
    "gate_milano": "Gate Milano",
    "gate_sardinia": "Gate Sardinia",
}


async def _fetch_partner_events(xceed_api_key: str) -> list[dict]:
    """Fetch all upcoming events from Xceed Partner API."""
    now_ts = int(time.time())
    headers = {"X-API-Key": xceed_api_key, "Accept": "application/json"}
    all_events = []
    offset = 0
    limit = 100
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                r = await client.get(
                    f"{PARTNER_BASE}/v1/events",
                    headers=headers,
                    params={"startingTime": now_ts, "limit": limit, "offset": offset},
                )
                r.raise_for_status()
                data = r.json()
                if not data.get("success"):
                    break
                batch = data.get("data", [])
                if not isinstance(batch, list):
                    break
                all_events.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.error("Xceed API error (offset=%d): %s", offset, e)
                break
    return all_events


async def _fetch_event_offers(xceed_api_key: str, event_uuid: str) -> dict:
    """Fetch ticket offers for a single event."""
    headers = {"X-API-Key": xceed_api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{PARTNER_BASE}/v2/events/{event_uuid}/offers",
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("data", {})
        except Exception as e:
            logger.debug("Could not fetch offers for %s: %s", event_uuid, e)
            return {}


def _build_event_document(event: dict, venue_label: str, offers: dict) -> tuple[str, dict]:
    name = event.get("name", "Evento")
    uuid = event.get("uuid", event.get("id", ""))

    # Date
    date_raw = event.get("date", event.get("start_date", ""))
    try:
        if isinstance(date_raw, (int, float)):
            dt = datetime.fromtimestamp(date_raw, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(date_raw).replace("Z", "+00:00"))
        date_str = dt.strftime("%-d %B %Y, ore %H:%M")
    except Exception:
        date_str = str(date_raw) or "Data da definire"

    # Ticket tiers from offers
    ticket_lines = []
    xceed_url = event.get("xceedUrl", event.get("url", ""))
    for category in ("tickets", "guestlist", "bottleservice"):
        items = offers.get(category, [])
        if not isinstance(items, list):
            items = list(items.values()) if isinstance(items, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            t_name = item.get("name", "")
            t_price = item.get("price", item.get("basePrice", ""))
            sold_out = item.get("soldOut", False) or item.get("available", True) is False
            if t_price is not None:
                avail = " (ESAURITO)" if sold_out else ""
                ticket_lines.append(f"  - {t_name}: €{t_price}{avail}")

    tickets_str = "\nBiglietti:\n" + "\n".join(ticket_lines) if ticket_lines else ""
    if xceed_url:
        tickets_str += f"\nAcquista: {xceed_url}"

    document = (
        f"EVENTO: {name}\n"
        f"Venue: {venue_label}\n"
        f"Data: {date_str}"
        f"{tickets_str}"
    ).strip()

    metadata = {
        "type": "event",
        "event_name": name,
        "date": str(date_raw),
        "venue": venue_label,
        "uuid": uuid,
    }
    return document, metadata


async def sync_all_venues():
    """Fetch upcoming events from Xceed and upsert into ChromaDB."""
    if not settings.xceed_api_key:
        logger.warning("XCEED_API_KEY non configurata — sync saltato")
        return

    logger.info("Avvio sync Xceed...")
    all_events = await _fetch_partner_events(settings.xceed_api_key)
    logger.info("Xceed: %d eventi totali ricevuti", len(all_events))

    venue_event_ids: dict[str, list[str]] = {k: [] for k in XCEED_VENUE_FILTER}

    for event in all_events:
        # Match event to venue by name
        event_venue_raw = event.get("venue", {})
        if isinstance(event_venue_raw, dict):
            event_venue_name = event_venue_raw.get("name", "")
        else:
            event_venue_name = str(event_venue_raw)

        matched_key = None
        for venue_key, venue_label in XCEED_VENUE_FILTER.items():
            if venue_label.lower() in event_venue_name.lower():
                matched_key = venue_key
                break

        if matched_key is None:
            # Also try matching by event name field if venue field empty
            for venue_key, venue_label in XCEED_VENUE_FILTER.items():
                if venue_label.lower() in event.get("name", "").lower():
                    matched_key = venue_key
                    break

        if matched_key is None:
            continue

        event_uuid = str(event.get("uuid", event.get("id", "")))
        if not event_uuid:
            continue

        offers = await _fetch_event_offers(settings.xceed_api_key, event_uuid)
        doc, meta = _build_event_document(event, XCEED_VENUE_FILTER[matched_key], offers)
        chromadb_manager.upsert_event(matched_key, event_uuid, doc, meta)
        venue_event_ids[matched_key].append(event_uuid)

    for venue_key, event_ids in venue_event_ids.items():
        chromadb_manager.delete_stale_events(venue_key, event_ids)
        logger.info("Sync completato per %s: %d eventi", XCEED_VENUE_FILTER[venue_key], len(event_ids))

    logger.info("Sync Xceed completato.")
