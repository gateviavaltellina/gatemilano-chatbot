"""Giudizio eval sull'abbonamento Claude Code invece che via API metered.

L'eval costa caro per il GIUDICE (output fino a 2000 token × 125 casi, input non
cachato): ~$10 di un run da ~$13. La generazione delle risposte (prompt statico
cachato) è la metà economica e va tenuta su API per fedeltà (vero bot Sonnet).

Flusso a 3 fasi che azzera il costo API del giudice:
  1. `python -m eval.run --export-replies` → genera le risposte via API e scrive
     un file repliche (id, rubrica, risposta, assertion_failures), SENZA giudice.
  2. Claude Code (sessione su abbonamento) legge il file, giudica ogni caso
     contro la rubrica e scrive un file verdetti: {id: {verdict, violated, reasoning}}.
  3. `assemble_results(repliche, verdetti)` produce il JSON risultati standard,
     leggibile da `eval.report`.
"""
from __future__ import annotations

from datetime import datetime, timezone


def build_replies_export(cases, results) -> dict:
    """Da (cases, results) allineati per indice → payload repliche per il giudice.
    Include tutto il necessario a giudicare senza altre chiamate: rubrica + risposta."""
    out = []
    for case, r in zip(cases, results):
        out.append({
            "id": r.id,
            "category": r.category,
            "venue": case.venue,
            "user_message": r.user_message,
            "reply": r.reply,
            "rubric": {"must": list(case.rubric.must), "must_not": list(case.rubric.must_not)},
            "assertion_failures": list(r.assertion_failures),
            "error": r.error,
            # comodo per il giudice: salta i casi già decisi o senza rubrica
            "needs_judge": not case.rubric.is_empty() and not r.error and not r.assertion_failures,
        })
    return {"cases": out}


def _passed(case: dict, verdict: dict | None) -> bool:
    """Stessa logica di CaseResult.passed, ma su dict (repliche da file)."""
    if case.get("error"):
        return False
    if case.get("assertion_failures"):
        return False
    rubric = case.get("rubric") or {}
    if not rubric.get("must") and not rubric.get("must_not"):
        return True  # nessuna rubrica → niente giudice → pass
    if not verdict:
        return False  # rubrica da valutare ma verdetto mancante → fail prudente
    return verdict.get("verdict") == "pass"


def assemble_results(replies: dict, verdicts: dict, model: str, timestamp: str | None = None) -> dict:
    """Unisce repliche (file) + verdetti (Claude Code) nel formato di eval.run.save_results."""
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cases = []
    for c in replies.get("cases", []):
        v = verdicts.get(c["id"])
        rubric = c.get("rubric") or {}
        has_rubric = bool(rubric.get("must") or rubric.get("must_not"))
        judge = None
        if has_rubric and not c.get("error") and not c.get("assertion_failures") and v:
            judge = {
                "verdict": v.get("verdict", "fail"),
                "violated": list(v.get("violated", [])),
                "reasoning": v.get("reasoning", ""),
            }
        cases.append({
            "id": c["id"],
            "category": c.get("category", ""),
            "user_message": c.get("user_message", ""),
            "reply": c.get("reply", ""),
            "assertion_failures": list(c.get("assertion_failures", [])),
            "judge": judge,
            "error": c.get("error"),
            "passed": _passed(c, v),
        })
    return {"timestamp": ts, "model": model, "judge": "claude-code-subscription", "cases": cases}
