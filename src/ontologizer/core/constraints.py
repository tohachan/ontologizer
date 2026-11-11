"""Constraints: what a project guarantees, forbids or does not promise, stated in the terms of its concepts.

A constraint is a reified claim, as a relation is. It is not an OWL axiom: an invariant is a norm the system
may break, and the store holds types, not the system's runtime state, so there is nothing to check it against.
What the ontology keeps is the statement, its modality, the concepts it constrains and its evidence.

Its classification follows SBVR: a *structural* rule is alethic (it cannot be otherwise: necessity,
impossibility), an *operative* rule is deontic (it can be broken: obligation, prohibition, permission). A
*boundary* (non-guarantee) is what the project says it does not promise. The kind follows from the modality.

The statement marks its terms: `[[id]]` or `[[id|text]]` names a concept (a local id, or `domain:<id>`,
`task:<id>`, … as elsewhere in the extraction), `[[?text]]` a term that has no concept yet (a candidate,
reported so the next extraction can add one). Every constrained concept is a marked term of the statement.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

MODALITIES = {"necessity": "structural", "impossibility": "structural",
              "obligation": "operative", "prohibition": "operative", "permission": "operative",
              "non-guarantee": "boundary"}
KINDS = ("structural", "operative", "boundary")
# local names of the kit individuals (kit/vocab.ttl)
MODALITY_TERMS = {"necessity": "necessity", "impossibility": "impossibility", "obligation": "obligation",
                  "prohibition": "prohibition", "permission": "permission", "non-guarantee": "nonGuarantee"}
KIND_TERMS = {"structural": "structuralRule", "operative": "operativeRule", "boundary": "promiseBoundary"}
PHRASE = {"necessity": "it is necessary that", "impossibility": "it is impossible that",
          "obligation": "it is obligatory that", "prohibition": "it is prohibited that",
          "permission": "it is permitted that", "non-guarantee": "it is not promised that"}

TERM = re.compile(r"\[\[(\?)?([^\[\]|]*)(?:\|([^\[\]]*))?\]\]")


def terms(statement: str) -> list[tuple[str, str | None, bool]]:
    """The marked terms in order: (ref or candidate text, display text, is_candidate)."""
    out = []
    for m in TERM.finditer(statement or ""):
        cand, ref, text = bool(m.group(1)), m.group(2).strip(), m.group(3)
        out.append((ref, text.strip() if text is not None else None, cand))
    return out


def refs(statement: str) -> list[str]:
    """The concept references a statement marks, unique, in order."""
    seen: list[str] = []
    for ref, _, cand in terms(statement):
        if not cand and ref not in seen:
            seen.append(ref)
    return seen


def candidates(statement: str) -> list[str]:
    """The candidate terms (`[[?text]]`): terms the statement needs that have no concept yet."""
    seen: list[str] = []
    for ref, _, cand in terms(statement):
        if cand and ref not in seen:
            seen.append(ref)
    return seen


def markup_errors(statement: str) -> list[str]:
    """What is wrong with the markup itself: unbalanced brackets, empty references."""
    errs = []
    s = statement or ""
    if s.count("[[") != s.count("]]") or len(TERM.findall(s)) != s.count("[["):
        errs.append("unbalanced or malformed [[...]] markup")
    for ref, text, cand in terms(s):
        if not ref:
            errs.append("a [[...]] term with nothing in it" if not cand else "a [[?...]] candidate with no text")
        elif cand and text is not None:
            errs.append(f"[[?{ref}|...]]: a candidate term takes no display text")
        elif not cand and any(ch.isspace() for ch in ref):
            errs.append(f"[[{ref}]]: a reference is a concept id; write a term without a concept as [[?{ref}]]")
    return errs


def _shown(ref: str) -> str:
    return ref.rsplit(":", 1)[-1].replace("-", " ")


def plain(statement: str, label: Callable[[str], str | None] | None = None) -> str:
    """The statement as prose: a term reads as its display text, else the concept's label, else its id."""
    def sub(m: re.Match) -> str:
        cand, ref, text = bool(m.group(1)), m.group(2).strip(), m.group(3)
        if text is not None:
            return text.strip()
        if cand:
            return ref
        return (label(ref) if label else None) or _shown(ref)
    return TERM.sub(sub, statement or "")


def remap(k: dict, new_ref: Callable[[str], str | None]) -> tuple[dict | None, list[str]]:
    """The constraint with every concept reference passed through `new_ref` (a new reference, or None when the
    concept is gone). A gone term is unmarked into plain text; a gone constrained concept leaves the list; a
    constraint left constraining nothing is dropped (None). Returns it with what changed, in words."""
    out = copy.deepcopy(k)
    notes: list[str] = []

    def sub(m: re.Match) -> str:
        cand, ref, text = bool(m.group(1)), m.group(2).strip(), m.group(3)
        if cand:
            return m.group(0)
        new = new_ref(ref)
        if new is None:
            notes.append(f"term {ref} unmarked: its concept is gone")
            return text.strip() if text is not None else _shown(ref)
        if new == ref:
            return m.group(0)
        return f"[[{new}|{text.strip()}]]" if text is not None else f"[[{new}]]"

    out["statement"] = TERM.sub(sub, k.get("statement") or "")
    kept = []
    for ref in k.get("constrains") or []:
        new = new_ref(ref)
        if new is None:
            notes.append(f"no longer constrains {ref}: it is gone")
        elif new not in kept:
            kept.append(new)
    out["constrains"] = kept
    if not kept:
        return None, notes + ["it constrains nothing left"]
    return out, notes


def prune(ks: list[dict], new_ref: Callable[[str], str | None]) -> tuple[list[dict], list[dict], list[dict]]:
    """`remap` over a list: (the constraints kept, those dropped with their reasons, those whose references
    changed with what changed). Unchanged constraints are returned as they are."""
    kept, gone, changed = [], [], []
    for k in ks:
        k2, notes = remap(k, new_ref)
        if k2 is None:
            gone.append({"id": k.get("id"), "reason": "; ".join(notes)})
            continue
        if k2 != k:
            changed.append({"id": k.get("id"), "changes": notes or ["references moved"]})
            kept.append(k2)
        else:
            kept.append(k)
    return kept, gone, changed


def kind_of(k: dict) -> str | None:
    return MODALITIES.get(k.get("modality"))


def claim_text(project: str, k: dict, label: Callable[[str], str | None] | None = None) -> str:
    """The constraint as a claim to check against its evidence."""
    phrase = PHRASE.get(k.get("modality"), k.get("modality") or "")
    return f"In the project {project}, {phrase}: '{plain(k.get('statement', ''), label)}'"


def same(a: dict, b: dict) -> bool:
    """Two extractors' constraints are the same claim when they have the same modality, constrain the same
    concepts and cite the same lines. Mechanical, on purpose: anything looser is a decision to calibrate."""
    def cited(k: dict) -> set[tuple]:
        return {(e.get("path"), str(e.get("lines") or "")) for e in k.get("evidence", [])}
    return (a.get("modality") == b.get("modality") and sorted(a.get("constrains") or []) == sorted(b.get("constrains") or [])
            and bool(cited(a)) and cited(a) == cited(b))
