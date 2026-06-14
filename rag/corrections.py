"""Store delle correzioni staff: regole globali per venue, iniettate nel contesto.

Direttive in linguaggio naturale che lo staff aggiunge da Discord per correggere
il bot in tempo reale. Vedi docs/superpowers/specs/2026-06-14-staff-corrections-live-design.md.

Persistenza su {PERSIST_DIR}/corrections.json (write-through). In memoria se
PERSIST_DIR è vuoto (perso al riavvio, come persistence.py).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# Oltre questa soglia per venue, l'handler Discord avvisa di consolidare nella KB.
SOFT_CAP = 30

# venue -> list[correction]. None = non ancora caricato dal disco.
_store: dict[str, list[dict]] | None = None


def _path() -> Path | None:
    d = (settings.persist_dir or "").strip()
    return Path(d) / "corrections.json" if d else None


def _ensure_loaded() -> None:
    global _store
    if _store is not None:
        return
    _store = {}
    path = _path()
    if path and path.exists():
        try:
            _store = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.exception("Errore caricamento correzioni")
            _store = {}


def _save() -> None:
    path = _path()
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_store, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        logger.exception("Errore salvataggio correzioni")


def add_correction(venue: str, rule: str, example: dict, author: str) -> str:
    _ensure_loaded()
    cid = uuid.uuid4().hex[:8]
    _store.setdefault(venue, []).append({
        "id": cid,
        "venue": venue,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rule": rule,
        "author": author,
        "example": example or {},
    })
    _save()
    return cid


def list_corrections(venue: str | None = None) -> list[dict]:
    _ensure_loaded()
    if venue is not None:
        return list(_store.get(venue, []))
    out: list[dict] = []
    for items in _store.values():
        out.extend(items)
    return out


def remove_correction(correction_id: str) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for i, c in enumerate(items):
            if c["id"] == correction_id:
                items.pop(i)
                _save()
                return True
    return False


def get_correction(correction_id: str) -> dict | None:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id:
                return c
    return None


def set_case(correction_id: str, case: dict) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id:
                c["case"] = case
                c["case_status"] = "pending"
                _save()
                return True
    return False


def approve_case(correction_id: str) -> bool:
    _ensure_loaded()
    for items in _store.values():
        for c in items:
            if c["id"] == correction_id and c.get("case"):
                c["case_status"] = "approved"
                _save()
                return True
    return False


def get_approved_cases() -> list[dict]:
    _ensure_loaded()
    out: list[dict] = []
    for items in _store.values():
        for c in items:
            if c.get("case_status") == "approved" and c.get("case"):
                out.append(c["case"])
    return out


def get_rules_text(venue: str) -> str:
    _ensure_loaded()
    items = _store.get(venue, [])
    if not items:
        return ""
    lines = ["CORREZIONI STAFF (priorità massima — sovrascrivono qualsiasi regola precedente, KB inclusa):"]
    for c in items:
        lines.append(f"- {c['rule']}")
    return "\n".join(lines)


def reset() -> None:
    """Forza il reload dal disco al prossimo accesso (usato dai test e dopo restart)."""
    global _store
    _store = None
