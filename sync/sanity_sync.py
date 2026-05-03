import re
import httpx
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from rag.chromadb_manager import chromadb_manager

_ROME = ZoneInfo("Europe/Rome")

logger = logging.getLogger(__name__)

SANITY_API_VERSION = "2021-10-21"

SANITY_PROJECTS = {
    "gate_milano": {
        "project_id": "68pz8xfn",
        "dataset": "production",
        "label": "Gate Milano",
        "has_site_settings": True,
        "has_blog_posts": False,
    },
    "gate_sardinia": {
        "project_id": "1999xgdy",
        "dataset": "production",
        "label": "Gate Sardinia",
        "has_site_settings": False,
        "has_blog_posts": True,
    },
}

GROQ_EVENTS = """*[_type == "event" && date >= $today && defined(title) && title != "?????"] | order(date asc) {
  _id,
  title,
  date,
  venue,
  ticketUrl,
  isSoldOut,
  isSellingFast,
  genres
}"""

GROQ_SITE_SETTINGS = """*[_type == "siteSettings"][0] {
  venueName,
  description,
  tagline,
  address,
  email,
  bookingEmail,
  openingHours,
  instagram,
  googleMapsUrl
}"""

GROQ_BLOG_POSTS = """*[_type == "blogPost"] {
  _id,
  titleEn,
  bodyEn
}"""


async def _sanity_get(project_id: str, dataset: str, query: str, params: dict = None) -> dict:
    url = f"https://{project_id}.api.sanity.io/v{SANITY_API_VERSION}/data/query/{dataset}"
    req_params = {"query": query}
    if params:
        req_params.update(params)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=req_params)
        r.raise_for_status()
        return r.json()


_XCEED_ID_RE = re.compile(r"xceed\.me/[^/]+/[^/]+/event/[^/]+/(\d+)")

def _extract_xceed_id(ticket_url: str) -> str:
    m = _XCEED_ID_RE.search(ticket_url or "")
    return m.group(1) if m else ""


async def _fetch_dice_description(ticket_url: str) -> str:
    """Extract event description from Dice.fm JSON-LD. Returns empty string on failure."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(ticket_url)
            if r.status_code != 200:
                return ""
            blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.DOTALL)
            import json as _json
            for block in blocks:
                try:
                    data = _json.loads(block)
                    if data.get("@type") == "MusicEvent":
                        return (data.get("description") or "").strip()
                except Exception:
                    continue
    except Exception as e:
        logger.debug("Dice scrape failed for %s: %s", ticket_url, e)
    return ""


async def _fetch_xceed_enrichment(xceed_id: str, xceed_api_key: str) -> dict:
    """Returns {about, prices_str} for an Xceed event numeric ID. Never raises."""
    result = {"about": "", "prices_str": ""}
    if not xceed_id:
        return result
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://events.xceed.me/v1/events/{xceed_id}")
            if r.status_code != 200:
                return result
            data = r.json().get("data", {})
            result["about"] = (data.get("about") or "").strip()
            uuid = data.get("id", "")
            if not uuid or not xceed_api_key:
                return result
            r2 = await client.get(
                f"https://partner.xceed.me/v2/events/{uuid}/offers",
                headers={"X-API-Key": xceed_api_key},
            )
            if r2.status_code != 200:
                return result
            offers = r2.json().get("data", {})
            lines = []
            for cat in ("ticket", "guestlist"):
                for item in offers.get(cat, []):
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "")
                    if isinstance(name, dict):
                        name = name.get("it") or name.get("en") or ""
                    price = item.get("priceAmount")
                    sold_out = item.get("isSoldOut", False)
                    hidden = item.get("isHidden", False)
                    if hidden or price is None:
                        continue
                    avail = " (ESAURITO)" if sold_out else ""
                    lines.append(f"  - {name}: €{price}{avail}")
            result["prices_str"] = "\n".join(lines)
    except Exception as e:
        logger.debug("Xceed enrichment failed for id=%s: %s", xceed_id, e)
    return result


async def _fetch_events(project_id: str, dataset: str) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        data = await _sanity_get(project_id, dataset, GROQ_EVENTS, {"$today": f'"{today}"'})
        return data.get("result", []) or []
    except Exception as e:
        logger.error("Sanity events fetch error (project=%s): %s", project_id, e)
        return []


async def _fetch_site_settings(project_id: str, dataset: str) -> dict:
    try:
        data = await _sanity_get(project_id, dataset, GROQ_SITE_SETTINGS)
        return data.get("result") or {}
    except Exception as e:
        logger.error("Sanity siteSettings fetch error (project=%s): %s", project_id, e)
        return {}


async def _fetch_blog_posts(project_id: str, dataset: str) -> list[dict]:
    try:
        data = await _sanity_get(project_id, dataset, GROQ_BLOG_POSTS)
        return data.get("result", []) or []
    except Exception as e:
        logger.error("Sanity blogPosts fetch error (project=%s): %s", project_id, e)
        return []


def _format_date(date_str: str) -> str:
    if not date_str:
        return "Data da definire"
    try:
        # Handle both "2026-05-02T21:00:00.000Z" and "2026-05-02"
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            dt_rome = dt.astimezone(_ROME)
            return dt_rome.strftime("%-d %B %Y, ore %H:%M")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%-d %B %Y")
    except Exception:
        return date_str


def _build_document(event: dict, venue_label: str, xceed: dict = None) -> tuple[str, dict]:
    title = event.get("title", "Evento").strip()
    date_str = event.get("date", "")
    room = event.get("venue") or ""
    ticket_url = event.get("ticketUrl") or ""
    is_sold_out = event.get("isSoldOut") or False
    is_selling_fast = event.get("isSellingFast") or False
    genres = event.get("genres") or []

    date_fmt = _format_date(date_str)
    room_str = f"\nSala: {room}" if room else ""
    genres_str = f"\nGeneri: {', '.join(genres)}" if genres else ""

    ticket_str = ""
    if ticket_url:
        if is_sold_out:
            ticket_str = f"\nBiglietti: ESAURITI — {ticket_url}"
        elif is_selling_fast:
            ticket_str = f"\nBiglietti: 🔥 Sold out velocemente — Acquista: {ticket_url}"
        else:
            ticket_str = f"\nAcquista biglietti: {ticket_url}"

    xceed = xceed or {}
    prices_str = f"\nPrezzi:\n{xceed['prices_str']}" if xceed.get("prices_str") else ""
    about = xceed.get("about", "")
    about_str = f"\nDescrizione: {about[:600]}" if about else ""

    document = (
        f"EVENTO: {title}\n"
        f"Venue: {venue_label}"
        f"{room_str}\n"
        f"Data: {date_fmt}"
        f"{genres_str}"
        f"{about_str}"
        f"{prices_str}"
        f"{ticket_str}"
    ).strip()

    # date_ts: midnight UTC del giorno locale Europe/Rome — per filtraggio ChromaDB
    # Es: "2026-05-08T22:00Z" = "2026-05-09 00:00 CEST" → date_ts = May 9 midnight UTC
    date_ts = 0
    try:
        from datetime import datetime, timezone as tz
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            dt_rome = dt.astimezone(_ROME)
            date_ts = int(datetime(dt_rome.year, dt_rome.month, dt_rome.day, tzinfo=tz.utc).timestamp())
        else:
            date_ts = int(datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=tz.utc).timestamp())
    except Exception:
        pass

    metadata = {
        "type": "event",
        "source": "sanity",
        "event_name": title,
        "date": date_str,
        "date_ts": date_ts,
        "venue": venue_label,
        "sanity_id": event.get("_id", ""),
    }
    return document, metadata


def _portable_text_to_str(blocks: list) -> str:
    """Extract plain text from Sanity Portable Text block array."""
    if not blocks:
        return ""
    lines = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("_type") != "block":
            continue
        text = "".join(
            span.get("text", "") for span in block.get("children", [])
            if isinstance(span, dict) and span.get("_type") == "span"
        )
        if text.strip():
            lines.append(text.strip())
    return "\n\n".join(lines)


def _build_site_settings_document(settings: dict, venue_label: str) -> tuple[str, dict]:
    name = settings.get("venueName") or venue_label
    desc = settings.get("description") or ""
    tagline = settings.get("tagline") or ""
    addr = settings.get("address") or {}
    street = addr.get("street", "")
    city = addr.get("city", "")
    postal = addr.get("postalCode", "")
    email = settings.get("email") or ""
    booking_email = settings.get("bookingEmail") or ""
    hours = settings.get("openingHours") or ""
    ig = settings.get("instagram") or ""
    maps = settings.get("googleMapsUrl") or ""

    parts = [f"VENUE: {name}"]
    if tagline:
        parts.append(tagline)
    if desc:
        parts.append(desc)
    if street:
        parts.append(f"Indirizzo: {street}, {postal} {city}".strip(", "))
    if hours:
        parts.append(f"Orari: {hours}")
    if email:
        parts.append(f"Email: {email}")
    if booking_email and booking_email != email:
        parts.append(f"Booking: {booking_email}")
    if ig:
        parts.append(f"Instagram: @{ig}")
    if maps:
        parts.append(f"Google Maps: {maps}")

    document = "\n".join(parts)
    metadata = {
        "type": "site_settings",
        "source": "sanity",
        "venue": venue_label,
    }
    return document, metadata


def _build_blog_document(post: dict, venue_label: str) -> tuple[str, dict]:
    title = post.get("titleEn") or post.get("title") or "Info"
    body = _portable_text_to_str(post.get("bodyEn") or post.get("body") or [])
    document = f"{title}\n\n{body}".strip()
    metadata = {
        "type": "blog_post",
        "source": "sanity",
        "venue": venue_label,
        "sanity_id": post.get("_id", ""),
    }
    return document, metadata


async def sync_all_venues():
    logger.info("Avvio sync Sanity...")

    for venue_key, cfg in SANITY_PROJECTS.items():
        label = cfg["label"]
        project_id = cfg["project_id"]
        dataset = cfg["dataset"]

        # Events
        events = await _fetch_events(project_id, dataset)
        logger.info("Sanity: %d eventi futuri per %s", len(events), label)
        from config import settings as _settings
        current_ids = []
        for event in events:
            sanity_id = event.get("_id", "")
            if not sanity_id:
                continue
            ticket_url = event.get("ticketUrl", "")
            xceed_id = _extract_xceed_id(ticket_url)
            if xceed_id:
                xceed_data = await _fetch_xceed_enrichment(xceed_id, _settings.xceed_api_key)
            elif "dice.fm" in ticket_url:
                desc = await _fetch_dice_description(ticket_url)
                xceed_data = {"about": desc, "prices_str": ""}
            else:
                xceed_data = {"about": "", "prices_str": ""}
            doc, meta = _build_document(event, label, xceed_data)
            chromadb_manager.upsert_event(venue_key, sanity_id, doc, meta)
            current_ids.append(sanity_id)
        chromadb_manager.delete_stale_events(venue_key, current_ids, source="sanity")

        # Site settings (Milano only)
        if cfg.get("has_site_settings"):
            settings = await _fetch_site_settings(project_id, dataset)
            if settings:
                doc, meta = _build_site_settings_document(settings, label)
                chromadb_manager.upsert_event(venue_key, f"site_settings_{venue_key}", doc, meta)
                logger.info("Sync siteSettings per %s", label)

        # Blog posts (Sardinia only)
        if cfg.get("has_blog_posts"):
            posts = await _fetch_blog_posts(project_id, dataset)
            logger.info("Sanity: %d blog posts per %s", len(posts), label)
            for post in posts:
                post_id = post.get("_id", "")
                if not post_id:
                    continue
                body_text = _portable_text_to_str(post.get("bodyEn") or post.get("body") or [])
                if not body_text:
                    continue
                doc, meta = _build_blog_document(post, label)
                chromadb_manager.upsert_event(venue_key, post_id, doc, meta)

        logger.info("Sync Sanity completato per %s: %d eventi", label, len(current_ids))

    logger.info("Sync Sanity completato.")
