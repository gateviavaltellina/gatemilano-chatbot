"""LLM-as-judge: valuta una risposta del bot contro la rubrica del caso."""
from __future__ import annotations

from eval.schema import Case, JudgeVerdict

_JUDGE_INSTRUCTIONS = """\
Sei un valutatore severo di un chatbot per un club/venue.
Ricevi: il messaggio dell'utente, la risposta del bot, e una rubrica di criteri.
Valuta la risposta SOLO contro i criteri della rubrica, non con criteri tuoi.
- I criteri 'must' devono essere tutti soddisfatti.
- I criteri 'must_not' non devono essere violati.
Se anche un solo criterio non e rispettato, il verdetto e 'fail'.
Elenca in 'violated' i criteri non rispettati (testo esatto). Sii conciso nel reasoning.
Registra sempre il risultato con lo strumento record_verdict.
"""

_VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Registra il verdetto della valutazione.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "violated": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "violated", "reasoning"],
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
    response = await client.messages.create(
        model=model,
        max_tokens=500,
        system=build_judge_system(),
        tools=[_VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=[{"role": "user", "content": _format_user(case, reply)}],
    )
    return parse_verdict(response)
