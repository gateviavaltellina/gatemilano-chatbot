import httpx
import logging
import time
from datetime import datetime, timezone
from rag.chromadb_manager import chromadb_manager

logger = logging.getLogger(__name__)

SANITY_API_VERSION = "2021-10-21"

SANITY_PROJECTS = {
    "gate_milano": {
        "project_id": "68pz8xfn",
        "dataset": "production",
        "label": "Gate Milano",
    },
    "gate_sardinia": {
        "project_id": "1999xgdy",
        "dataset": "production",
        "label": "Gate Sardinia",
    },
}

GROQ_QUERY = """*[_type == "event" && date >= $today && defined(title) && title != "?????"] | order(date asc) {
  _id,
  title,
  date,
  venue,
  ticketUrl,
  isSoldOut,
  isSellingFast,
  genres
}"""


async def _fetch_events(project_id: str, dataset: str) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"https://{project_id}.api.sanity.io/v{SANITY_API_VERSION}/data/query/{dataset}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(url, params={"query": GROQ_QUERY, "$today": f'"{today}"'})
            r.raise_for_status()
            data = r.json()
            return data.get("result", []) or []
        except Exception as e:
            logger.error("Sanity fetch error (project=%s): %s", project_id, e)
            return []


def _format_date(date_str: str) -> str:
    if not date_str:
        return "Data da definire"
    try:
        # Handle both "2026-05-02T21:00:00.000Z" and "2026-05-02"
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%-d %B %Y, ore %H:%M")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%-d %B %Y")
    except Exception:
        return date_str


def _build_document(event: dict, venue_label: str) -> tuple[str, dict]:
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

    document = (
        f"EVENTO: {title}\n"
        f"Venue: {venue_label}"
        f"{room_str}\n"
        f"Data: {date_fmt}"
        f"{genres_str}"
        f"{ticket_str}"
    ).strip()

    metadata = {
        "type": "event",
        "event_name": title,
        "date": date_str,
        "venue": venue_label,
        "sanity_id": event.get("_id", ""),
    }
    return document, metadata


async def sync_all_venues():
    logger.info("Avvio sync Sanity...")

    for venue_key, cfg in SANITY_PROJECTS.items():
        label = cfg["label"]
        events = await _fetch_events(cfg["project_id"], cfg["dataset"])
        logger.info("Sanity: %d eventi futuri ricevuti per %s", len(events), label)

        current_ids = []
        for event in events:
            sanity_id = event.get("_id", "")
            if not sanity_id:
                continue
            doc, meta = _build_document(event, label)
            chromadb_manager.upsert_event(venue_key, sanity_id, doc, meta)
            current_ids.append(sanity_id)

        chromadb_manager.delete_stale_events(venue_key, current_ids)
        logger.info("Sync completato per %s: %d eventi", label, len(current_ids))

    logger.info("Sync Sanity completato.")
