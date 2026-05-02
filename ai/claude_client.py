import logging
from anthropic import AsyncAnthropic
from config import settings

logger = logging.getLogger(__name__)

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

VENUE_NAMES = {
    "gate_milano": "Gate Milano",
    "gate_sardinia": "Gate Sardinia",
}

SYSTEM_TEMPLATE = """\
Sei il chatbot ufficiale di {venue_name}, un club/venue eventi.
Rispondi SEMPRE nella lingua del messaggio dell'utente (italiano di default).
Tono: friendly, professionale, conciso. Non usare linguaggio eccessivamente formale.

INFORMAZIONI VENUE E EVENTI:
{rag_context}

REGOLE FONDAMENTALI:
- Usa SOLO le informazioni fornite sopra per rispondere. Se non sai, dillo chiaramente.
- Per biglietti includi SEMPRE il link diretto Xceed o Dice se disponibile nel contesto.
- Per richieste operative (oggetti smarriti, reclami, accrediti, tavoli VIP):
  indirizza a info@gatemilano.com o +39 391 487 6443
- Non inventare date, prezzi o lineup non presenti nel contesto.
- Risposte brevi e dirette — max 3-4 frasi salvo richieste dettagliate.
- Usa emoji con moderazione (1-2 max per messaggio).
"""

async def generate_response(
    venue: str,
    user_message: str,
    rag_context: str,
    history: list[dict],
) -> str:
    venue_name = VENUE_NAMES.get(venue, venue)
    system = SYSTEM_TEMPLATE.format(
        venue_name=venue_name,
        rag_context=rag_context or "Nessuna informazione specifica disponibile al momento.",
    )
    messages = [*history, {"role": "user", "content": user_message}]
    try:
        response = await _client.messages.create(
            model=settings.model,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("Errore Claude API: %s", e)
        return (
            "Mi dispiace, al momento non riesco a rispondere. "
            "Per assistenza contatta info@gatemilano.com o +39 391 487 6443."
        )
