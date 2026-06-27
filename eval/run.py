"""Runner dell'eval harness: esegue i casi e salva i risultati."""
from __future__ import annotations
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from eval.assertions import run_assertions
from eval.loader import load_cases
from eval.schema import Case, CaseResult

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"

# Prefisso della risposta di fallback di generate_response() quando l'API fallisce.
# Una risposta cosi' NON e' un fail comportamentale: e' un errore infra, da escludere dal punteggio.
BOT_FALLBACK_PREFIX = "Mi dispiace, al momento non riesco a rispondere"


async def run_case(case: Case, *, generate_fn, judge_fn) -> CaseResult:
    reply = await generate_fn(case.venue, case.user_message, case.rag_context, case.history)
    if reply.startswith(BOT_FALLBACK_PREFIX):
        return CaseResult(
            id=case.id, category=case.category, user_message=case.user_message,
            reply=reply, error="bot fallback (errore API, non valutato)",
        )
    failures = run_assertions(reply, case.assertions)
    if failures:
        return CaseResult(
            id=case.id, category=case.category, user_message=case.user_message,
            reply=reply, assertion_failures=failures, judge=None,
        )
    try:
        verdict = None if case.rubric.is_empty() else await judge_fn(case, reply)
    except Exception as e:  # errore del giudice (troncamento, rete) = infra, non un 'fail'
        return CaseResult(
            id=case.id, category=case.category, user_message=case.user_message,
            reply=reply, error=f"errore giudice (non valutato): {e}",
        )
    return CaseResult(
        id=case.id, category=case.category, user_message=case.user_message,
        reply=reply, assertion_failures=[], judge=verdict,
    )


async def run_all(cases, *, generate_fn, judge_fn, concurrency: int = 5) -> list[CaseResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(c):
        async with sem:
            return await run_case(c, generate_fn=generate_fn, judge_fn=judge_fn)

    return await asyncio.gather(*(_guarded(c) for c in cases))


def save_results(results: list[CaseResult], model: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{ts}.json"
    payload = {
        "timestamp": ts,
        "model": model,
        "cases": [asdict(r) | {"passed": r.passed} for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def main() -> int:
    from config import settings

    args = sys.argv[1:]

    # Modalità ASSEMBLE: nessuna chiamata API. Unisce le repliche (generate via API)
    # coi verdetti prodotti da Claude Code (giudice su abbonamento) → risultati standard.
    if "--assemble" in args:
        from eval.local_judge import assemble_results
        i = args.index("--assemble")
        try:
            replies_path, verdicts_path = args[i + 1], args[i + 2]
        except IndexError:
            print("Uso: python -m eval.run --assemble <repliche.json> <verdetti.json>")
            return 1
        replies = json.loads(Path(replies_path).read_text(encoding="utf-8"))
        verdicts = json.loads(Path(verdicts_path).read_text(encoding="utf-8"))
        payload = assemble_results(replies, verdicts, model="claude-code-subscription")
        RESULTS_DIR.mkdir(exist_ok=True)
        out = RESULTS_DIR / f"{payload['timestamp']}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        passed = sum(c["passed"] for c in payload["cases"])
        print(f"Assemblato: {passed}/{len(payload['cases'])} pass — {out}")
        return 0

    from anthropic import AsyncAnthropic
    from ai.claude_client import generate_response

    # max_retries alto: l'org puo' essere su un tier basso (es. 30k token/min),
    # i 429 vanno assorbiti col backoff dell'SDK invece di far fallire la run.
    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=8)

    # temperature=0: risposte deterministiche → la suite è riproducibile (no flakiness).
    async def generate_fn(venue, user_message, rag_context, history):
        return await generate_response(venue, user_message, rag_context, history, temperature=0)

    cases = load_cases(CASES_DIR)
    if not cases:
        print("Nessun caso trovato in", CASES_DIR)
        return 1
    # Concorrenza bassa di default per non sforare il rate limit; override via env.
    concurrency = int(os.getenv("EVAL_CONCURRENCY", "2"))

    # Modalità EXPORT: solo generazione (API, metà economica col prompt cachato),
    # NIENTE giudice → file repliche da far giudicare a Claude Code sull'abbonamento.
    # Azzera la voce di costo più cara dell'eval (output del giudice ~$10/run).
    if "--export-replies" in args:
        from eval.local_judge import build_replies_export

        async def no_judge(case, reply):
            return None

        print(f"Genero {len(cases)} risposte (export, senza giudice, concurrency={concurrency})...")
        results = await run_all(cases, generate_fn=generate_fn, judge_fn=no_judge, concurrency=concurrency)
        payload = build_replies_export(cases, results)
        RESULTS_DIR.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = RESULTS_DIR / f"{ts}_replies.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        n_judge = sum(c["needs_judge"] for c in payload["cases"])
        print(f"Repliche salvate in {out} — {n_judge}/{len(cases)} casi da giudicare con Claude Code.")
        return 0

    from eval.judge import judge_reply
    judge_model = settings.model  # Sonnet

    async def judge_fn(case, reply):
        return await judge_reply(case, reply, client=client, model=judge_model)

    print(f"Eseguo {len(cases)} casi (concurrency={concurrency})...")
    results = await run_all(cases, generate_fn=generate_fn, judge_fn=judge_fn, concurrency=concurrency)
    path = save_results(results, model=settings.model)
    passed = sum(r.passed for r in results)
    print(f"Risultati: {passed}/{len(results)} pass — salvati in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
