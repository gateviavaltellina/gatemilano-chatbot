"""Genera un eval case di regressione da una correzione staff, via LLM (tool-use)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DRAFT_INSTRUCTIONS = """\
Sei un generatore di test per un chatbot di un club/venue.
Ricevi: una REGOLA staff (il comportamento corretto da garantire), il messaggio utente
che ha innescato la correzione, e la risposta SBAGLIATA che il bot aveva dato.
Genera un eval case minimale che PASSA se il bot segue la regola e FALLISCE sulla
risposta sbagliata. Criteri verificabili e concisi.
- 'must': cosa la risposta DEVE fare per rispettare la regola.
- 'must_not': cosa NON deve fare (deriva dalla risposta sbagliata).
- 'rag_context': lascia "" salvo che la regola richieda dati di un evento; in tal caso
  scrivi un contesto sintetico minimo.
- 'forbidden_substrings': stringhe esatte vietate, solo se ovvie (es. un'email sbagliata).
Registra il risultato con lo strumento draft_eval_case.
"""

_DRAFT_TOOL = {
    "name": "draft_eval_case",
    "description": "Registra l'eval case generato.",
    "input_schema": {
        "type": "object",
        "properties": {
            "user_message": {"type": "string"},
            "rag_context": {"type": "string"},
            "must": {"type": "array", "items": {"type": "string"}},
            "must_not": {"type": "array", "items": {"type": "string"}},
            "forbidden_substrings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["user_message", "must", "must_not"],
    },
}


def _format_user(correction: dict) -> str:
    ex = correction.get("example") or {}
    return (
        f"REGOLA:\n{correction['rule']}\n\n"
        f"MESSAGGIO UTENTE:\n{ex.get('user_msg', '')}\n\n"
        f"RISPOSTA SBAGLIATA:\n{ex.get('wrong_reply', '')}"
    )


def _parse_tool(response) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


async def draft_case(correction: dict, *, client, model: str) -> dict | None:
    """Ritorna un eval case (schema eval) o None se la generazione fallisce."""
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=600,
            temperature=0,
            system=_DRAFT_INSTRUCTIONS,
            tools=[_DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "draft_eval_case"},
            messages=[{"role": "user", "content": _format_user(correction)}],
        )
    except Exception:
        logger.exception("draft_case: errore LLM per correzione %s", correction.get("id"))
        return None
    data = _parse_tool(response)
    if not data or not data.get("must"):
        return None
    ex = correction.get("example") or {}
    return {
        "id": f"corr-{correction['id']}",
        "category": "corrections",
        "venue": correction["venue"],
        "user_message": data.get("user_message") or ex.get("user_msg", ""),
        "rag_context": data.get("rag_context", "") or "",
        "rubric": {
            "must": data.get("must", []),
            "must_not": data.get("must_not", []),
        },
        "assertions": {
            "forbidden_substrings": data.get("forbidden_substrings", []) or [],
        },
    }
