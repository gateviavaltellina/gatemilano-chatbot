"""Caricamento e validazione dei casi di test da file YAML."""
from __future__ import annotations

from pathlib import Path

import yaml

from eval.schema import Assertions, Case, Rubric

_REQUIRED = ("id", "category", "venue", "user_message")


class CaseValidationError(Exception):
    pass


def _parse_case(raw: dict, source: str) -> Case:
    for key in _REQUIRED:
        if not raw.get(key):
            raise CaseValidationError(f"{source}: caso senza campo obbligatorio {key!r}: {raw!r}")
    rubric_raw = raw.get("rubric") or {}
    assert_raw = raw.get("assertions") or {}
    return Case(
        id=raw["id"],
        category=raw["category"],
        venue=raw["venue"],
        user_message=raw["user_message"],
        rag_context=raw.get("rag_context", "") or "",
        history=raw.get("history") or [],
        rubric=Rubric(
            must=rubric_raw.get("must") or [],
            must_not=rubric_raw.get("must_not") or [],
        ),
        assertions=Assertions(
            forbidden_substrings=assert_raw.get("forbidden_substrings") or [],
            forbidden_markdown=bool(assert_raw.get("forbidden_markdown", False)),
        ),
    )


def load_cases(cases_dir) -> list[Case]:
    cases_dir = Path(cases_dir)
    cases: list[Case] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.yaml")):
        raw_list = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw_list, list):
            raise CaseValidationError(f"{path.name}: il file deve contenere una lista di casi")
        for raw in raw_list:
            case = _parse_case(raw, path.name)
            if case.id in seen:
                raise CaseValidationError(f"id duplicato: {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    return cases
