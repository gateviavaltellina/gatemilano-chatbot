"""Importa nel repo gli eval case approvati esposti dal bot.

Uso: python -m eval.import_correction_cases <base_url> --token <TOKEN>
Idempotente: salta gli id già presenti in eval/cases/corrections.yaml.
Gira in locale (usa httpx + pyyaml, dev-deps); non parte in produzione.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml

CASES_FILE = Path(__file__).parent / "cases" / "corrections.yaml"

# Campi che eval/loader.py richiede: un caso senza uno di questi farebbe rifiutare
# l'INTERO file al load. Li validiamo qui per scartare il singolo caso difettoso.
_REQUIRED = ("id", "category", "venue", "user_message")


def _fetch(base_url: str, token: str) -> list[dict]:
    url = base_url.rstrip("/") + "/eval/correction-cases"
    r = httpx.get(url, params={"key": token}, timeout=20)
    r.raise_for_status()
    return r.json().get("cases", [])


def _valid(cases: list[dict]) -> tuple[list[dict], int]:
    good = [c for c in cases if all(c.get(k) for k in _REQUIRED)]
    return good, len(cases) - len(good)


def _merge(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    seen = {c.get("id") for c in existing}
    added = [c for c in incoming if c.get("id") not in seen]
    return existing + added, len(added)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("base_url")
    p.add_argument("--token", required=True)
    args = p.parse_args(argv)

    incoming, skipped = _valid(_fetch(args.base_url, args.token))
    if skipped:
        print(f"⚠️ {skipped} casi scartati (campi obbligatori mancanti)")
    existing: list[dict] = []
    if CASES_FILE.exists():
        existing = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8")) or []
    merged, added = _merge(existing, incoming)
    CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CASES_FILE.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Importati {added} nuovi casi (totale {len(merged)}) in {CASES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
