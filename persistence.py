"""Persistenza leggera dello stato conversazioni su file.

Senza questo, lo store è solo in memoria: ogni deploy/riavvio Railway azzera
storia chat, sessioni di human takeover e le mappe Discord per il takeover.
Qui salviamo/ripristiniamo quello stato in un JSON su disco.

NON persistiamo lo store eventi (`rag.event_store`): viene ripopolato dal sync
all'avvio, quindi sarebbe ridondante.

- Path configurabile via PERSIST_DIR (vuoto = disabilitato). In produzione si
  punta a un volume Railway montato, es. PERSIST_DIR=/data.
- Scrittura atomica (file tmp + os.replace) per non corrompere lo stato.
- I dizionari/set vivi vengono mutati in place (clear + update), così i moduli
  che li referenziano continuano a vedere lo stesso oggetto.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def _state_path():
    d = (settings.persist_dir or "").strip()
    return Path(d) / "chatbot_state.json" if d else None


def save_state() -> bool:
    path = _state_path()
    if not path:
        return False
    try:
        from whatsapp import webhook as wa
        from instagram import webhook as ig
        from notifications import discord_bot as db

        data = {
            "wa_conversations": wa._conversations,
            "wa_drinklist_sent": list(wa._drinklist_sent),
            "ig_conversations": ig._ig_conversations,
            "human_sessions": db._human_sessions,
            "msg_to_phone": db._msg_to_phone,
            "msg_context": db._msg_context,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        logger.info(
            "Stato salvato (%d conv WA, %d conv IG, %d takeover)",
            len(data["wa_conversations"]), len(data["ig_conversations"]), len(data["human_sessions"]),
        )
        return True
    except Exception:
        logger.exception("Errore salvataggio stato")
        return False


def load_state() -> bool:
    path = _state_path()
    if not path or not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        from whatsapp import webhook as wa
        from instagram import webhook as ig
        from notifications import discord_bot as db

        def _restore(live, saved):
            live.clear()
            live.update(saved or {})

        _restore(wa._conversations, data.get("wa_conversations"))
        wa._drinklist_sent.clear()
        wa._drinklist_sent.update(data.get("wa_drinklist_sent") or [])
        _restore(ig._ig_conversations, data.get("ig_conversations"))
        _restore(db._human_sessions, data.get("human_sessions"))
        _restore(db._msg_to_phone, data.get("msg_to_phone"))
        _restore(db._msg_context, data.get("msg_context"))
        logger.info(
            "Stato caricato (%d conv WA, %d conv IG, %d takeover)",
            len(wa._conversations), len(ig._ig_conversations), len(db._human_sessions),
        )
        return True
    except Exception:
        logger.exception("Errore caricamento stato")
        return False
