"""
VIP table availability and checkout links via Xceed Partner API.
URL pattern: xceed.me/en/{city}/checkout/bottleService/{slug}/{numeric_id}/{offer_uuid}?channel={channel}
"""
import re
import time
import logging
import httpx
from config import settings

logger = logging.getLogger(__name__)

_XCEED_URL_RE = re.compile(r"xceed\.me/[^/]+/[^/]+/event/([^/]+)/(\d+)")
_PARTNER_BASE = "https://partner.xceed.me"

# Cache: numeric_id → {"tables": list, "ts": float}
_cache: dict[int, dict] = {}
_CACHE_TTL = 1800  # 30 minuti


def _extract_slug_id(ticket_url: str) -> tuple[str, int] | tuple[None, None]:
    m = _XCEED_URL_RE.search(ticket_url or "")
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


async def _fetch_uuid_for_numeric_id(numeric_id: int, api_key: str) -> str | None:
    """Find the Xceed UUID for a given numeric event ID via Partner API."""
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    now = int(time.time())
    async with httpx.AsyncClient(timeout=20) as client:
        for start_time in (now - 86400 * 30, now - 86400 * 180):
            try:
                r = await client.get(
                    f"{_PARTNER_BASE}/v1/events",
                    params={"startingTime": start_time, "limit": 200},
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                for e in data.get("data", []):
                    if e.get("id") == numeric_id:
                        return e.get("uuid")
            except Exception as e:
                logger.debug("Partner API error fetching events: %s", e)
    return None


async def _fetch_bottleservice(event_uuid: str, api_key: str) -> list[dict]:
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{_PARTNER_BASE}/v2/events/{event_uuid}/offers",
                headers=headers,
            )
            r.raise_for_status()
            return r.json().get("data", {}).get("bottleservice", [])
        except Exception as e:
            logger.debug("Error fetching bottleservice for %s: %s", event_uuid, e)
            return []


async def get_vip_tables_context(ticket_url: str, channel: str = "gate-milano") -> str:
    """
    Given a Sanity ticketUrl pointing to Xceed, returns a formatted string
    with available VIP tables and their checkout links for RAG injection.
    Returns empty string if no Xceed URL or API key missing.
    """
    if not settings.xceed_api_key or not ticket_url:
        return ""

    slug, numeric_id = _extract_slug_id(ticket_url)
    if not slug or not numeric_id:
        return ""

    cached = _cache.get(numeric_id)
    if cached and time.time() - cached["ts"] < _CACHE_TTL:
        tables = cached["tables"]
    else:
        event_uuid = await _fetch_uuid_for_numeric_id(numeric_id, settings.xceed_api_key)
        if not event_uuid:
            logger.warning("VIP tables: UUID non trovato per numeric_id=%d", numeric_id)
            return ""
        bs_offers = await _fetch_bottleservice(event_uuid, settings.xceed_api_key)
        tables = []
        for t in bs_offers:
            name_obj = t.get("name", {})
            name = (name_obj.get("en") or name_obj.get("it") or "") if isinstance(name_obj, dict) else str(name_obj)
            price = t.get("onlinePrice", 0)
            sold_out = t.get("isSoldOut", False)
            status = t.get("status", "")
            available = not sold_out and status not in ("sales_closed",)
            offer_uuid = t.get("id", "")
            link = f"https://xceed.me/en/milano/checkout/bottleService/{slug}/{numeric_id}/{offer_uuid}?channel={channel}"
            tables.append({"name": name, "price": price, "available": available, "link": link})
        _cache[numeric_id] = {"tables": tables, "ts": time.time()}

    if not tables:
        return ""

    available = [t for t in tables if t["available"]]

    unavailable = [t for t in tables if not t["available"]]

    lines = ["TAVOLI VIP DISPONIBILI:"]
    for t in available:
        lines.append(f"- {t['name']}: €{t['price']} → Prenota: {t['link']}")
    for t in unavailable:
        lines.append(f"- {t['name']}: €{t['price']} — NON DISPONIBILE")

    if not available:
        lines = ["TAVOLI VIP: tutti esauriti per questo evento."]

    return "\n".join(lines)


def invalidate_vip_cache() -> None:
    """Clear the VIP tables cache — call after Xceed sync so sold-out status is fresh."""
    _cache.clear()
    logger.debug("VIP tables cache invalidata")
