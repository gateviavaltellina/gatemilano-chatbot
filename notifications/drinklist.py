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

# MENU FOOD COURT (pizze, tramezzini, focacce, toast, hot dog) — ancora diverso da
# drink e bottle list. Per ora solo Sardegna.
FOOD_MENUS: dict[str, tuple[str, str]] = {
    "gate_sardinia": (f"{DRINKLIST_BASE}/food_list_sardegna.pdf", "Food List Gate Sardinia.pdf"),
}

# Trigger IMPLICITI bottle list nel TESTO UTENTE: se l'utente nomina tavoli/VIP →
# invio proattivo, una sola volta. NB: "perreo xl" NON è qui — è un NOME DI EVENTO,
# non un segnale di tavoli.
_DRINKLIST_TRIGGERS = ["tavolo", "tavoli", "vip", "bottle", "bottiglia", "minimo"]
# Trigger nella RISPOSTA del bot: SOLO quando il bot offre/promette la drinklist
# stessa (allinea la promessa all'azione). NON parole generiche come "tavoli" o
# "vip": la chiusura-tipo "Vuoi info su biglietti, tavoli o altro?" le contiene e
# faceva partire la drinklist su quasi ogni risposta (spam reale, 2 volte in uno
# stesso screenshot). L'interesse dell'utente per i tavoli è già coperto dai
# trigger sul SUO testo.
_DRINKLIST_REPLY_TRIGGERS = [
    "drinklist", "drink list", "lista bottiglie", "listino bottiglie",
    "carta bottiglie", "bottle list",
]
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
    if not already_sent:
        if any(t in lower_text for t in _DRINKLIST_TRIGGERS):
            return True
        # Nella risposta del bot solo i segnali forti di tavoli (no "vip"/nomi evento)
        if any(t in lower_reply for t in _DRINKLIST_REPLY_TRIGGERS):
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


# --- Menu Food Court (pizze, panini, ecc.) ---

# Richieste ESPLICITE del cibo → invia il menu food. Termini food-specifici (niente
# sovrapposizioni con drink/bottle list).
_FOOD_MENU_TRIGGERS = [
    "cibo", "mangiare", "da mangiare", "si mangia", "food", "food court",
    "menu food", "menù food", "paninoteca", "panino", "panini", "pizza", "pizze",
    "toast", "hot dog", "hotdog", "tramezzino", "tramezzini", "focaccia", "focacce",
    "wurstel", "fame",
]

# Marcatori nella RISPOSTA del bot che indicano che sta offrendo il menu food: così
# se il bot dice "food court" il PDF/link parte davvero.
_FOOD_MENU_REPLY_MARKERS = ["food court", "menu food", "menù food", "menu del cibo"]


def should_send_food_menu(venue: str, lower_text: str, lower_reply: str = "") -> bool:
    """True se l'utente chiede del cibo / dei prezzi food, o se è il bot a offrirlo
    nella risposta. I prezzi sono già nella KB: il PDF si manda solo su richiesta."""
    if venue not in FOOD_MENUS:
        return False
    if any(t in lower_text for t in _FOOD_MENU_TRIGGERS):
        return True
    return bool(lower_reply) and any(m in lower_reply for m in _FOOD_MENU_REPLY_MARKERS)


def get_food_menu(venue: str) -> tuple[str, str] | None:
    return FOOD_MENUS.get(venue)


def food_menu_link_message(venue: str) -> str | None:
    item = FOOD_MENUS.get(venue)
    if not item:
        return None
    url, _ = item
    return f"Ecco il menu del Food Court con tutti i prezzi 🍕\n{url}"
