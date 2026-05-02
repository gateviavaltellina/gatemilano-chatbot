import logging
from datetime import datetime, timezone
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
Tono: friendly, diretto, conciso. Come un addetto all'ingresso che conosce tutto.

DATA E ORA ATTUALE: {current_datetime} (fuso orario Europe/Rome)
Usa questa informazione per rispondere correttamente a domande come "stasera", "questo weekend", "domani", ecc.

INFORMAZIONI VENUE E EVENTI:
{rag_context}

REGOLE FONDAMENTALI:
- Usa SOLO le informazioni fornite sopra per rispondere. Se non sai, dillo chiaramente.
- Cita SEMPRE il nome esatto dell'evento quando parli di serate specifiche.
- Per biglietti includi SEMPRE il link diretto se disponibile nel contesto.
- Per richieste operative (oggetti smarriti, reclami, accrediti, tavoli VIP):
  indirizza a info@gatemilano.com o +39 391 487 6443
- Non inventare date, prezzi o lineup non presenti nel contesto.
- Risposte brevi e dirette — MAX 3 frasi. Non elencare mai più di 3 punti.
- USA AL MASSIMO 1 EMOJI per messaggio. Spesso zero è meglio.
- NON usare mai formattazione markdown: niente asterischi, niente bullet points, niente grassetto.
- Scrivi testo semplice, come un SMS. WhatsApp non renderizza il markdown correttamente.
- Rispondi prima alla domanda specifica, poi aggiungi info utili se necessario.
"""

async def generate_response(
    venue: str,
    user_message: str,
    rag_context: str,
    history: list[dict],
) -> str:
    venue_name = VENUE_NAMES.get(venue, venue)
    current_datetime = datetime.now(timezone.utc).strftime("%A %-d %B %Y, %H:%M UTC")
    system = SYSTEM_TEMPLATE.format(
        venue_name=venue_name,
        rag_context=rag_context or "Nessuna informazione specifica disponibile al momento.",
        current_datetime=current_datetime,
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
