import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)


_MILANO_IDS = {"35517015101275600", "17841405933946552"}
_SARDINIA_IDS = {"24588954374135134", "17841452139166980"}

# ID Instagram business "inviabile" via Graph API (graph.facebook.com): l'endpoint
# di invio /{id}/messages richiede l'IG business account id (17841...), NON gli id
# app-scoped storici (35517.../24588...) che il token System User non risolve. Il
# webhook può consegnare l'uno o l'altro: normalizziamo SEMPRE su questo per l'invio.
_MILANO_SEND_ID = "17841405933946552"
_SARDINIA_SEND_ID = "17841452139166980"


def _token_for_account(ig_account_id: str) -> str:
    # Legge dal token_store (token rinnovato più recente), non da settings diretto.
    from instagram import token_store
    if ig_account_id in _MILANO_IDS:
        return token_store.get("gate_milano")
    if ig_account_id in _SARDINIA_IDS:
        return token_store.get("gate_sardinia")
    return ""


def _send_id_for_account(ig_account_id: str) -> str:
    """ID da usare NELL'URL di invio.

    Solo per l'API via Facebook (graph.facebook.com) l'endpoint richiede l'IG
    business id (17841...). Con l'API Instagram Login (graph.instagram.com, il
    setup nativo di questa app) si usa invece l'id consegnato dal webbook così
    com'è. Condizioniamo sull'host per non rompere nessuno dei due percorsi."""
    if "facebook.com" not in (settings.ig_api_url or ""):
        return ig_account_id
    if ig_account_id in _MILANO_IDS:
        return _MILANO_SEND_ID
    if ig_account_id in _SARDINIA_IDS:
        return _SARDINIA_SEND_ID
    return ig_account_id


# Limite reale dell'API Instagram: 1000 caratteri UTF-8 per messaggio.
# Oltre, l'API rifiuta l'invio e il cliente NON riceve nulla. Margine di sicurezza.
_IG_TEXT_LIMIT = 950


def split_for_ig(text: str, limit: int = _IG_TEXT_LIMIT) -> list[str]:
    """Spezza un testo lungo in blocchi <= limit, tagliando sui confini naturali
    (paragrafo > riga > frase > spazio) così ogni messaggio resta leggibile."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return [p for p in parts if p]


async def _post_ig_payload(ig_account_id: str, token: str, payload: dict, what: str) -> bool:
    """POST all'API IG con UN retry sugli errori transitori (timeout / 5xx).
    Gli errori permanenti (4xx: token scaduto, testo rifiutato) non si ritentano."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    send_id = _send_id_for_account(ig_account_id)
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in (1, 2):
            try:
                r = await client.post(
                    f"{settings.ig_api_url}/{send_id}/messages",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return True
            except httpx.HTTPStatusError as e:
                body = e.response.text[:500]
                if e.response.status_code >= 500 and attempt == 1:
                    logger.warning("IG %s: HTTP %s al tentativo 1, riprovo — %s", what, e.response.status_code, body)
                    continue
                logger.error("Errore IG %s: %s — %s", what, e, body)
                return False
            except Exception as e:
                if attempt == 1:
                    logger.warning("IG %s: errore transitorio al tentativo 1, riprovo — %s", what, e)
                    continue
                logger.error("Errore IG %s: %s", what, e)
                return False
    return False


async def send_ig_message(ig_account_id: str, recipient_id: str, text: str) -> bool:
    """Invia un testo su IG spezzandolo se supera il limite API (1000 char).
    Ritorna False se ANCHE UN SOLO blocco non parte: il chiamante deve avvisare
    lo staff, perché il cliente non ha ricevuto (tutta) la risposta."""
    token = _token_for_account(ig_account_id)
    if not token:
        logger.warning("Nessun token IG per account %s", ig_account_id)
        return False
    chunks = split_for_ig(text)
    if not chunks:
        return False
    for chunk in chunks:
        payload = {"recipient": {"id": recipient_id}, "message": {"text": chunk}}
        if not await _post_ig_payload(ig_account_id, token, payload, f"invio a {recipient_id}"):
            return False
    logger.info("IG reply inviato a %s via account %s (%d parti)", recipient_id, ig_account_id, len(chunks))
    return True


async def react_to_message(ig_account_id: str, recipient_id: str, message_id: str, reaction: str = "love") -> bool:
    """Mette una reaction (emoji) a un messaggio IG, senza inviare testo.
    Usato per menzioni/post nelle storie e per le reaction.
    API Meta: POST /{ig_id}/messages con sender_action=react."""
    token = _token_for_account(ig_account_id)
    if not token:
        logger.warning("Nessun token IG per account %s", ig_account_id)
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "recipient": {"id": recipient_id},
        "sender_action": "react",
        "payload": {"message_id": message_id, "reaction": reaction},
    }
    send_id = _send_id_for_account(ig_account_id)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{settings.ig_api_url}/{send_id}/messages",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            logger.info("IG reaction '%s' a %s (msg %s)", reaction, recipient_id, message_id[:14])
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore reaction IG a %s: %s — %s", recipient_id, e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore reaction IG: %s", e)
            return False
