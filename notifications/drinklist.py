"""Drinklist VIP condivisa tra i canali.

WhatsApp può allegare il PDF come documento. Instagram NO (le DM IG non
supportano allegati documenti), quindi su IG si invia il LINK al PDF come testo.
Fonte unica di config qui, usata da whatsapp/webhook.py e instagram/webhook.py.
"""
from __future__ import annotations

DRINKLIST_BASE = "https://gatemilano-chatbot-production.up.railway.app/static"

# venue → (url PDF, filename mostrato all'utente). Venue assente → nessun invio.
DRINKLISTS: dict[str, tuple[str, str]] = {
    "gate_milano": (f"{DRINKLIST_BASE}/drinklist_perreo.pdf", "Drinklist VIP Perreo.pdf"),
    "gate_sardinia": (f"{DRINKLIST_BASE}/drinklist_sardegna.pdf", "Drinklist VIP Gate Sardinia.pdf"),
}

# Trigger IMPLICITI: si parla di tavoli/VIP → invio proattivo, una sola volta.
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "bottle", "bottiglia", "minimo", "perreo xl"]
# Richieste ESPLICITE del listino → invia SEMPRE (anche se già inviato).
_DRINKLIST_EXPLICIT = [
    "drinklist", "drink list", "listino", "bottiglie", "lista bottiglie",
    "lista drink", "carta bottiglie", "carta drink",
]


def should_send_drinklist(venue: str, lower_text: str, lower_reply: str, already_sent: bool) -> bool:
    """Richiesta esplicita → sempre; trigger implicito (si parla di tavoli/VIP) →
    solo se non già inviato a questo utente."""
    if venue not in DRINKLISTS:
        return False
    if any(t in lower_text for t in _DRINKLIST_EXPLICIT):
        return True
    if not already_sent and any(t in lower_text or t in lower_reply for t in _DRINKLIST_TRIGGERS):
        return True
    return False


def get_drinklist(venue: str) -> tuple[str, str] | None:
    """(url, filename) per la venue, o None se non configurata."""
    return DRINKLISTS.get(venue)


def drinklist_link_message(venue: str) -> str | None:
    """Messaggio testuale col link alla drinklist, per i canali senza allegati (IG).
    None se la venue non è configurata."""
    item = DRINKLISTS.get(venue)
    if not item:
        return None
    url, _ = item
    return f"Ecco la drinklist VIP 🍾 scegli pure da qui:\n{url}"
