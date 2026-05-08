import asyncio
import httpx
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config import settings
from rag.event_store import upsert_event, delete_stale_events
from rag.vip_tables import invalidate_vip_cache

_ROME = ZoneInfo("Europe/Rome")

logger = logging.getLogger(__name__)

PARTNER_BASE = "https://partner.xceed.me"
EVENTS_BASE = "https://events.xceed.me"
OFFER_BASE = "https://offer.xceed.me"

# Open Event API channel slugs per venue (public API, no auth required)
XCEED_CHANNELS = {
    "gate_milano": "gate-milano",
    "gate_sardinia": "gate-sardinia",  # seasonal — may return 0 events off-season
}

XCEED_VENUE_LABELS = {
    "gate_milano": "Gate Milano",
    "gate_sardinia": "Gate Sardinia",
}


async def _fetch_open_events(channel: str) -> list[dict]:
    """Fetch upcoming events from Xceed Open Event API (no auth, startTime filter works correctly)."""
    now_ts = int(time.time())
    all_events = []
    offset = 0
    limit = 100
    max_pages = 50  # Guard against infinite pagination loop
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(max_pages):
            try:
                r = await client.get(
                    f"{EVENTS_BASE}/v1/events",
                    params={
                        "channel": channel,
                        "startTime": now_ts,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                if r.status_code == 404:
                    logger.debug("Channel %s not found on Xceed Open API", channel)
                    break
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
                logger.error("Xceed Open API error (channel=%s, offset=%d): %s", channel, offset, e)
                break
        else:
            logger.warning("Xceed pagination hit max_pages=%d per channel=%s", max_pages, channel)
    return all_events


async def _fetch_event_offers(xceed_api_key: str, event_uuid: str) -> dict:
    """Fetch ticket offers for a single event via Partner API (with exponential backoff on 429)."""
    headers = {"X-API-Key": xceed_api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            try:
                r = await client.get(
                    f"{PARTNER_BASE}/v2/events/{event_uuid}/offers",
                    headers=headers,
                )
                if r.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Xceed 429 rate limit (offer %s) — retry in %ds", event_uuid, wait)
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json().get("data", {})
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.debug("Could not fetch offers for %s: %s", event_uuid, e)
                return {}
            except Exception as e:
                logger.debug("Could not fetch offers for %s: %s", event_uuid, e)
                return {}
    return {}


def _build_event_document(event: dict, venue_label: str, offers: dict) -> tuple[str, dict]:
    name = event.get("name", "Evento")
    uuid = event.get("id", event.get("uuid", ""))

    # Date — Open Event API uses startingTime (Unix timestamp)
    date_raw = event.get("startingTime", 0)
    date_ts = 0
    try:
        dt = datetime.fromtimestamp(int(date_raw), tz=timezone.utc)
        date_str = dt.strftime("%-d %B %Y, ore %H:%M")
        dt_rome = dt.astimezone(_ROME)
        date_ts = int(datetime(dt_rome.year, dt_rome.month, dt_rome.day, tzinfo=timezone.utc).timestamp())
    except Exception:
        date_str = "Data da definire"

    # Lineup from Open Event API
    lineup = event.get("lineup", [])
    artists = [a.get("name", "") for a in lineup if isinstance(a, dict) and not a.get("isGeneric")]
    lineup_str = f"\nArtisti: {', '.join(artists)}" if artists else ""

    # Music genres
    genres = [g.get("name", "") for g in event.get("musicGenres", []) if isinstance(g, dict)]
    genres_str = f"\nGeneri: {', '.join(genres)}" if genres else ""

    # Ticket tiers from Partner API offers
    ticket_lines = []
    xceed_url = event.get("externalSalesUrl", "")
    for category in ("ticket", "guestlist", "bottleservice", "tickets"):
        items = offers.get(category, [])
        if not isinstance(items, list):
            items = list(items.values()) if isinstance(items, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            # name can be a localized dict {"it": ..., "en": ...} or a plain string
            raw_name = item.get("name", "")
            if isinstance(raw_name, dict):
                t_name = raw_name.get("it") or raw_name.get("en") or next(
                    (v for v in raw_name.values() if v), ""
                )
            else:
                t_name = raw_name
            t_price = item.get("priceAmount", item.get("onlinePrice", ""))
            sold_out = item.get("isSoldOut", False) or item.get("soldOut", False)
            if t_price is not None and t_price != "":
                avail = " (ESAURITO)" if sold_out else ""
                ticket_lines.append(f"  - {t_name}: €{t_price}{avail}")

    tickets_str = "\nBiglietti:\n" + "\n".join(ticket_lines) if ticket_lines else ""
    if xceed_url:
        tickets_str += f"\nAcquista: {xceed_url}"

    document = (
        f"EVENTO: {name}\n"
        f"Venue: {venue_label}\n"
        f"Data: {date_str}"
        f"{lineup_str}"
        f"{genres_str}"
        f"{tickets_str}"
    ).strip()

    metadata = {
        "type": "event",
        "source": "xceed",
        "event_name": name,
        "date": str(date_raw),
        "date_ts": date_ts,
        "venue": venue_label,
        "uuid": uuid,
    }
    return document, metadata


async def sync_all_venues():
    """Fetch upcoming events from Xceed Open Event API and upsert into ChromaDB."""
    logger.info("Avvio sync Xceed...")

    venue_event_ids: dict[str, list[str]] = {k: [] for k in XCEED_CHANNELS}

    for venue_key, channel_slug in XCEED_CHANNELS.items():
        venue_label = XCEED_VENUE_LABELS[venue_key]
        events = await _fetch_open_events(channel_slug)
        logger.info("Xceed Open API: %d eventi ricevuti per %s", len(events), venue_label)

        for event in events:
            event_uuid = str(event.get("id", event.get("uuid", "")))
            if not event_uuid:
                continue

            # Fetch offers via Partner API if key available
            offers = {}
            if settings.xceed_api_key:
                offers = await _fetch_event_offers(settings.xceed_api_key, event_uuid)

            doc, meta = _build_event_document(event, venue_label, offers)
            upsert_event(venue_key, event_uuid, doc, meta)
            venue_event_ids[venue_key].append(event_uuid)

        delete_stale_events(venue_key, venue_event_ids[venue_key], source="xceed")
        logger.info("Sync completato per %s: %d eventi", venue_label, len(venue_event_ids[venue_key]))

    invalidate_vip_cache()
    logger.info("Sync Xceed completato.")
