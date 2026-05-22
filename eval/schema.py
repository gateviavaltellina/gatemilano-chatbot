"""Strutture dati dell'eval harness (solo dati, nessuna logica I/O)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Assertions:
    forbidden_substrings: list[str] = field(default_factory=list)
    forbidden_markdown: bool = False


@dataclass
class Rubric:
    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.must and not self.must_not


@dataclass
class Case:
    id: str
    category: str
    venue: str
    user_message: str
    rag_context: str = ""
    history: list[dict] = field(default_factory=list)
    rubric: Rubric = field(default_factory=Rubric)
    assertions: Assertions = field(default_factory=Assertions)


@dataclass
class JudgeVerdict:
    verdict: str  # "pass" | "fail"
    violated: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class CaseResult:
    id: str
    category: str
    user_message: str
    reply: str
    assertion_failures: list[str] = field(default_factory=list)
    judge: JudgeVerdict | None = None
    usage: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if self.assertion_failures:
            return False
        if self.judge is None:
            return True
        return self.judge.verdict == "pass"
