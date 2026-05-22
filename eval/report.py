"""Report dei risultati eval: tabella per categoria + diff vs run precedente."""
from __future__ import annotations
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def load_results(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(run: dict) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for c in run["cases"]:
        agg = out.setdefault(c["category"], [0, 0])
        agg[1] += 1
        if c["passed"]:
            agg[0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def diff_runs(prev: dict, curr: dict) -> dict:
    prev_pass = {c["id"]: c["passed"] for c in prev["cases"]}
    regressions, improvements = [], []
    for c in curr["cases"]:
        was = prev_pass.get(c["id"])
        if was is None:
            continue
        if was and not c["passed"]:
            regressions.append(c["id"])
        elif not was and c["passed"]:
            improvements.append(c["id"])
    return {"regressions": regressions, "improvements": improvements}


def render(run: dict, prev: dict | None = None) -> str:
    lines = [f"Run {run['timestamp']} — modello {run['model']}", ""]
    total_p = total_t = 0
    for cat, (p, t) in sorted(summarize(run).items()):
        lines.append(f"  {cat:20} {p}/{t}")
        total_p += p
        total_t += t
    lines.append(f"  {'TOTALE':20} {total_p}/{total_t}")
    fails = [c for c in run["cases"] if not c["passed"]]
    if fails:
        lines += ["", "FALLITI:"]
        for c in fails:
            why = ", ".join(c["assertion_failures"]) or (
                ", ".join((c["judge"] or {}).get("violated", [])) if c["judge"] else "?")
            lines.append(f"  [{c['category']}] {c['id']}: {why}")
    if prev:
        d = diff_runs(prev, run)
        lines += ["", f"vs run precedente — regressioni: {d['regressions'] or 'nessuna'} | "
                      f"miglioramenti: {d['improvements'] or 'nessuno'}"]
    return "\n".join(lines)


def _latest_two() -> tuple[Path | None, Path | None]:
    files = sorted(RESULTS_DIR.glob("*.json"))
    if not files:
        return None, None
    if len(files) == 1:
        return files[-1], None
    return files[-1], files[-2]


def main() -> int:
    want_diff = "--diff" in sys.argv
    latest, prev = _latest_two()
    if latest is None:
        print("Nessun risultato in", RESULTS_DIR)
        return 1
    curr = load_results(latest)
    prev_run = load_results(prev) if (want_diff and prev) else None
    print(render(curr, prev_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
