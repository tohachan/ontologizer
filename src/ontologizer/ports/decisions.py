"""Decision port: narrow, typed questions with probabilistic answers.

Three question types:
- `choice`: pick one of named options; the answer is the option and a distribution;
- `score`: place on an ordered scale of ≥2 levels; the answer is the level index;
- `noul`: a yes/no question; the answer is P(true).

An adapter answers a batch of questions about one state (the context the
questions are asked about). Answers carry probabilities and, when the
provider reports it, a confidence in [0, 1].
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

TYPES = ("choice", "score", "noul")


@dataclass(frozen=True)
class Question:
    id: str
    type: str                                   # one of TYPES
    instructions: str
    options: dict[str, str | None] | None = None   # choice: option → description
    levels: tuple[str, ...] | None = None          # score: ordered levels
    true: str | None = None                        # noul: what true / false mean (optional, both or none)
    false: str | None = None

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"question {self.id}: type one of {TYPES}")
        if self.type == "choice" and not self.options:
            raise ValueError(f"question {self.id}: choice needs options")
        if self.type == "score" and (not self.levels or len(self.levels) < 2):
            raise ValueError(f"question {self.id}: score needs ≥2 levels")
        if self.type == "noul" and (self.true is None) != (self.false is None):
            raise ValueError(f"question {self.id}: noul criteria need both true and false")

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if "levels" in d:
            d["levels"] = list(d["levels"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        d = dict(d)
        if d.get("levels") is not None:
            d["levels"] = tuple(d["levels"])
        return cls(**d)


@dataclass
class Answer:
    id: str
    type: str
    value: str | int | float | None            # choice: option; score: level index; noul: P(true)
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None
    error: str | None = None                   # refused / failed: no invented answer

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None

    def yes(self, threshold: float = 0.5) -> bool:
        """noul: P(true) ≥ threshold."""
        return self.ok and float(self.value) >= threshold

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, {}, [])}

    @classmethod
    def from_dict(cls, d: dict) -> "Answer":
        kw = {k: d[k] for k in ("id", "type", "value", "probabilities", "confidence", "error") if k in d}
        kw.setdefault("value", None)
        return cls(**kw)


@dataclass
class Receipt:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    request_id: str | None = None


class DecisionAdapter(Protocol):
    name: str
    model: str

    def decide(self, state: str | dict, questions: list[Question]) -> tuple[list[Answer], Receipt]:
        """Answer every question about `state`. A refusal or failure is an Answer with `error`."""
        ...


def key(stage: str, state: str | dict, q: Question) -> str:
    """Stable key of one question about one state, for caches, logs and gold sets."""
    blob = json.dumps({"stage": stage, "state": state, "q": q.to_dict()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
