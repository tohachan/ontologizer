"""Deterministic candidate generation for pair decisions: which pairs are worth asking about.

Code proposes, the decision port judges. Scores here only rank and filter
candidates; they never decide that two concepts are the same.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher

STOP = {"a", "an", "the", "of", "and", "or", "to", "for", "in", "on", "by", "with", "is", "it", "its",
        "that", "this", "as", "be", "are", "from", "at", "which", "each", "one", "when", "into", "not"}


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z][a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower()):
        if w not in STOP:
            out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def names(c: dict) -> list[str]:
    return [c.get("label", ""), *c.get("alt_labels", []), c.get("id", "").replace("-", " ")]


def label_score(a: dict, b: dict) -> float:
    best = 0.0
    for x in names(a):
        for y in names(b):
            if x and y:
                best = max(best, SequenceMatcher(None, norm(x), norm(y)).ratio())
    return best


def same_label(a: dict, b: dict) -> bool:
    return bool({norm(x) for x in names(a) if x} & {norm(y) for y in names(b) if y})


def evidence_paths(c: dict) -> set[str]:
    return {e.get("path") for e in c.get("evidence", []) if e.get("path")}


def pair_score(a: dict, b: dict) -> float:
    """Within one project: shared inventory ids, shared evidence files, label similarity."""
    return (0.4 * jaccard(a.get("inventory", []), b.get("inventory", []))
            + 0.3 * jaccard(evidence_paths(a), evidence_paths(b))
            + 0.3 * label_score(a, b))


class TfIdf:
    """Cosine similarity of label + definition across a corpus of concepts."""

    def __init__(self, docs: dict[str, dict]):
        self.tf = {k: Counter(tokens(" ".join(names(c)) * 2 + " " + c.get("definition", ""))) for k, c in docs.items()}
        df = Counter(t for tf in self.tf.values() for t in tf)
        n = max(1, len(self.tf))
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
        self.vec = {k: self._norm({t: f * self.idf[t] for t, f in tf.items()}) for k, tf in self.tf.items()}

    @staticmethod
    def _norm(v: dict) -> dict:
        s = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / s for t, x in v.items()}

    def sim(self, a: str, b: str) -> float:
        va, vb = self.vec[a], self.vec[b]
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(t, 0.0) for t, x in va.items())
