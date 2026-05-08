"""Centralized static knowledge cache shared by WhatsApp and Instagram webhooks."""
import logging
from config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
_cache: dict[str, str] = {}


def get(venue: str) -> str:
    if venue not in _cache:
        try:
            _cache[venue] = (KNOWLEDGE_DIR / f"{venue}.md").read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Knowledge file non trovato per %s: %s", venue, e)
            _cache[venue] = ""
    return _cache[venue]


def invalidate(venue: str | None = None) -> None:
    """Clear cache for a specific venue, or all venues if venue is None."""
    if venue:
        _cache.pop(venue, None)
        logger.debug("Knowledge cache invalidata per %s", venue)
    else:
        _cache.clear()
        logger.info("Knowledge cache invalidata (tutti i venue)")
