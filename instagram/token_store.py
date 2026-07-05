"""Store dinamico dei token Instagram, con persistenza sul volume.

I token Instagram Login scadono in ~60 giorni: un job periodico (token_refresh)
li rinnova e li salva QUI, così sopravvivono ai riavvii. L'invio legge sempre da
qui (non da settings diretto), così usa l'ultimo token rinnovato.

Regola anti-sovrascrittura (rotazione manuale): se lo staff cambia a mano la
variabile d'ambiente, quel valore vince sul token persistito — riconosciamo il
cambio confrontando l'`origin` (il valore env da cui discende il token attivo).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

VENUES = ("gate_milano", "gate_sardinia")

_tokens: dict[str, str] = {}
_origin: dict[str, str] = {}   # valore env da cui discende il token attivo per venue


def _env_token(venue: str) -> str:
    tok = settings.ig_gatemilano_token if venue == "gate_milano" else settings.ig_gatesardinia_token
    return tok or ""


def _path() -> Path | None:
    d = (settings.persist_dir or "").strip()
    return Path(d) / "ig_tokens.json" if d else None


def load() -> None:
    """Popola lo store all'avvio.

    Regola: si usa il token persistito (rinnovato) TRANNE quando è avvenuta una
    rotazione MANUALE, cioè quando l'env è valorizzata e DIVERSA dall'origin da cui
    il persistito discende. Un'env vuota NON conta come rotazione (potrebbe essere un
    glitch di caricamento secret): in quel caso si tiene comunque il token persistito
    valido, per non perderlo."""
    saved = {}
    p = _path()
    if p and p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            saved = raw if isinstance(raw, dict) else {}
            if not isinstance(raw, dict):
                logger.warning("token_store: contenuto non-oggetto, ignoro e uso le env var")
        except Exception:
            logger.exception("token_store: file persistito illeggibile, uso le env var")
    for v in VENUES:
        env = _env_token(v)
        entry = saved.get(v) if isinstance(saved.get(v), dict) else {}
        persisted = entry.get("token") or ""
        origin = entry.get("origin") or ""
        manual_rotation = bool(env) and origin != env
        if persisted and not manual_rotation:
            _tokens[v] = persisted          # token rinnovato (o env vuota = glitch: non buttarlo)
            _origin[v] = origin or env
        else:
            _tokens[v] = env                # rotazione manuale (env nuova != origin), o niente persistito
            _origin[v] = env
    logger.info(
        "token_store caricato (milano=%s, sardinia=%s)",
        "set" if _tokens.get("gate_milano") else "vuoto",
        "set" if _tokens.get("gate_sardinia") else "vuoto",
    )


def get(venue: str) -> str:
    """Token attivo per la venue. Fallback all'env se lo store non è stato caricato."""
    return _tokens.get(venue) or _env_token(venue)


def set_token(venue: str, token: str) -> None:
    """Aggiorna il token attivo (post-rinnovo) e persiste. `origin` resta invariato:
    è il valore env da cui questa catena di rinnovi discende."""
    if not token:
        return
    _tokens[venue] = token
    _origin.setdefault(venue, _env_token(venue))
    _save()


def _save() -> None:
    p = _path()
    if not p:
        return  # nessun volume: solo in memoria (si ricade sull'env al riavvio)
    try:
        data = {v: {"token": _tokens.get(v, ""), "origin": _origin.get(v, _env_token(v))}
                for v in VENUES}
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)   # scrittura atomica
        except Exception:
            # niente .tmp orfani (es. disco pieno): rimuovi prima di propagare
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        logger.exception("token_store: salvataggio fallito")
