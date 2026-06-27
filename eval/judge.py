"""LLM-as-judge: valuta una risposta del bot contro la rubrica del caso."""
from __future__ import annotations

from eval.schema import Case, JudgeVerdict


class JudgeTruncated(RuntimeError):
    """Il giudice ha esaurito max_tokens prima di emettere il verdetto: l'input
    del tool_use e' incompleto, quindi va trattato come errore infra (non un 'fail')."""

_JUDGE_INSTRUCTIONS = """\
Sei un valutatore severo di un chatbot per un club/venue.
Ricevi: il messaggio dell'utente, la risposta del bot, e una rubrica di criteri.
Valuta la risposta SOLO contro i criteri della rubrica, non con criteri tuoi.
- I criteri 'must' devono essere tutti soddisfatti.
- I criteri 'must_not' non devono essere violati.
Se anche un solo criterio non e rispettato, il verdetto e 'fail'.
Un criterio 'must_not' e violato SOLO se la risposta contiene esplicitamente il problema descritto.
Se la risposta e naturale e non contiene la frase/comportamento vietato, il criterio e RISPETTATO.
Non penalizzare per cio' che la risposta omette: valuta solo cio' che dice davvero.
Elenca in 'violated' i criteri non rispettati (testo esatto). Sii conciso nel reasoning.
Compila i campi in ordine: prima 'reasoning' (ragiona e concludi), poi 'violated', poi 'verdict'.
Il 'verdict' DEVE essere coerente con la conclusione del reasoning: se il reasoning conclude che i criteri sono rispettati, verdict='pass'.
Registra sempre il risultato con lo strumento record_verdict.
"""

_VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Registra il verdetto della valutazione.",
    "input_schema": {
        "type": "object",
        "properties": {
            # reasoning PRIMA: il judge ragiona, poi conclude — evita verdetti
            # incoerenti col ragionamento (verdict generato prima del reasoning).
            "reasoning": {"type": "string", "description": "Ragiona qui PRIMA di decidere; chiudi con la decisione finale."},
            "violated": {"type": "array", "items": {"type": "string"}},
            "verdict": {"type": "string", "enum": ["pass", "fail"], "description": "Coerente con la conclusione del reasoning."},
        },
        "required": ["reasoning", "violated", "verdict"],
    },
}


def build_judge_system() -> list[dict]:
    """System come blocchi: prefisso statico cacheabile."""
    return [{"type": "text", "text": _JUDGE_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}]


def _format_user(case: Case, reply: str) -> str:
    must = "\n".join(f"- {m}" for m in case.rubric.must) or "(nessuno)"
    must_not = "\n".join(f"- {m}" for m in case.rubric.must_not) or "(nessuno)"
    return (
        f"MESSAGGIO UTENTE:\n{case.user_message}\n\n"
        f"RISPOSTA DEL BOT:\n{reply}\n\n"
        f"CRITERI must (devono valere):\n{must}\n\n"
        f"CRITERI must_not (non devono valere):\n{must_not}"
    )


def parse_verdict(response) -> JudgeVerdict:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input
            return JudgeVerdict(
                verdict=data.get("verdict", "fail"),
                violated=data.get("violated", []),
                reasoning=data.get("reasoning", ""),
            )
    return JudgeVerdict(verdict="fail", violated=["judge: nessun tool_use nella risposta"], reasoning="")


async def judge_reply(case: Case, reply: str, *, client, model: str) -> JudgeVerdict:
    # max_tokens ampio: lo schema mette 'reasoning' PRIMA del verdict, quindi serve
    # spazio per ragionamento + violated + verdict. Con un budget stretto (es. 500)
    # il reasoning lungo lo esauriva e il tool_use usciva troncato → verdict='fail'
    # silenzioso e violated/reasoning vuoti. 2000 copre i casi osservati con margine.
    response = await client.messages.create(
        model=model,
        max_tokens=2000,
        temperature=0,  # giudizio deterministico, riduce i falsi positivi
        system=build_judge_system(),
        tools=[_VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=[{"role": "user", "content": _format_user(case, reply)}],
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise JudgeTruncated(f"giudizio troncato (max_tokens) per il caso {case.id}")
    return parse_verdict(response)
