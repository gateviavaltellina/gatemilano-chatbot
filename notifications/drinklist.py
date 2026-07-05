"""Drinklist VIP condivisa tra i canali.

WhatsApp può allegare il PDF come documento. Instagram NO (le DM IG non
supportano allegati documenti), quindi su IG si invia il LINK al PDF come testo.
Fonte unica di config qui, usata da whatsapp/webhook.py e instagram/webhook.py.
"""
from __future__ import annotations

DRINKLIST_BASE = "https://gatemilano-chatbot-production.up.railway.app/static"

# venue → (url PDF, filename mostrato all'utente). Venue assente → nessun invio.
# Questa è la BOTTLE LIST dei tavoli VIP (bottiglie intere, bottle service).
DRINKLISTS: dict[str, tuple[str, str]] = {
    "gate_milano": (f"{DRINKLIST_BASE}/drinklist_perreo.pdf", "Drinklist VIP Perreo.pdf"),
    "gate_sardinia": (f"{DRINKLIST_BASE}/drinklist_sardegna.pdf", "Drinklist VIP Gate Sardinia.pdf"),
}

# CARTA DRINK à la carte (prezzi dei singoli drink al bar) — cosa DIVERSA dalla bottle
# list dei tavoli. Per ora solo Sardegna.
DRINK_MENUS: dict[str, tuple[str, str]] = {
    "gate_sardinia": (f"{DRINKLIST_BASE}/drink_menu_sardegna.pdf", "Drink List Gate Sardinia.pdf"),
}

# Trigger IMPLICITI bottle list: si parla di tavoli/VIP → invio proattivo, una sola volta.
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "bottle", "bottiglia", "minimo", "perreo xl"]
# Richieste ESPLICITE della bottle list dei tavoli → invia SEMPRE. "drinklist"/"drink
# list" restano qui: nel sistema indicano storicamente la bottle list VIP dei tavoli.
_DRINKLIST_EXPLICIT = [
    "drinklist", "drink list", "bottiglie", "lista bottiglie", "listino bottiglie",
    "carta bottiglie", "bottle list", "prezzi bottiglie", "quali bottiglie",
]

# Richieste ESPLICITE della carta drink (prezzi dei SINGOLI drink al bar) → invia il
# menu drink. Frasi price-centriche, distinte dalla bottle list (nessun "drinklist").
_DRINK_MENU_TRIGGERS = [
    "quanto costa un drink", "quanto costano i drink", "quanto viene un drink",
    "quanto costa bere", "prezzi drink", "prezzo drink", "prezzo dei drink",
    "menu drink", "menù drink", "drink menu", "carta drink", "carta dei drink",
    "lista drink", "listino drink",
    "quanto costa un cocktail", "quanto costano i cocktail", "prezzi cocktail",
    "quanto costa un mojito", "quanto costa un gin",
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


# --- Carta drink à la carte (prezzi singoli drink al bar) ---

# Marcatori nella RISPOSTA del bot che indicano che sta offrendo la carta drink:
# così se il bot dice "ti mando la carta drink" il link parte davvero (allineiamo la
# promessa all'azione). Solo termini drink-specifici, non "drinklist" (= bottle list).
_DRINK_MENU_REPLY_MARKERS = [
    "carta drink", "menu drink", "menù drink", "lista drink", "listino drink", "prezzi drink",
]


def should_send_drink_menu(venue: str, lower_text: str, lower_reply: str = "") -> bool:
    """True se l'utente chiede la carta drink / i prezzi dei drink, o se è il bot a
    offrirla nella risposta. I prezzi sono già nella KB: il PDF si manda solo su
    richiesta (niente invio proattivo)."""
    if venue not in DRINK_MENUS:
        return False
    if any(t in lower_text for t in _DRINK_MENU_TRIGGERS):
        return True
    return bool(lower_reply) and any(m in lower_reply for m in _DRINK_MENU_REPLY_MARKERS)


def get_drink_menu(venue: str) -> tuple[str, str] | None:
    return DRINK_MENUS.get(venue)


def drink_menu_link_message(venue: str) -> str | None:
    item = DRINK_MENUS.get(venue)
    if not item:
        return None
    url, _ = item
    return f"Ecco la drink list completa con tutti i prezzi 🍸\n{url}"
