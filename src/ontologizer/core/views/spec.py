"""ViewSpec, version 1: what a view shows (docs/visualizer.md, View specification).

`parse` checks the JSON Schema and the faults that need no data: a seed that is also excluded, an
empty selection without a level, a repeated id, an entity in two groups. Those are rejected in
every mode. `check` then resolves the scope and every reference against a source model:

- strict, for anything being created or changed: an unresolved reference rejects the spec;
- tolerant, for a saved view reopened on changed data: each one becomes a diagnostic, and the spec
  is returned unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from ontologizer.core.config import Config
from ontologizer.core.levels import Preset
from ontologizer.core.views.models import SourceModel
from ontologizer.core.views.scope import GraphEntry, Scope, resolve

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "schemas" / "views" / "view-spec.v1.json"
MODES = ("strict", "tolerant")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    path: str


class SpecError(ValueError):
    def __init__(self, issues: Sequence[Issue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in self.issues))


@dataclass(frozen=True)
class Step:
    relationship_id: str
    direction: str


@dataclass(frozen=True)
class Group:
    id: str
    title: str
    members: tuple[str, ...]
    collapsed: bool


@dataclass(frozen=True)
class Summary:
    id: str
    steps: tuple[Step, ...]
    label: str | None
    hide_intermediate: bool


@dataclass(frozen=True)
class Annotation:
    id: str
    text: str
    target_kind: str
    target_id: str


@dataclass(frozen=True)
class ViewSpec:
    id: str
    title: str
    primary_graphs: tuple[str, ...]
    context_graphs: tuple[str, ...]
    entities: tuple[str, ...]
    excluded: tuple[str, ...]
    levels: tuple[str, ...]
    relation_kinds: tuple[str, ...]
    relation_types: tuple[str, ...]
    hops: int
    direction: str
    groups: tuple[Group, ...]
    summaries: tuple[Summary, ...]
    annotations: tuple[Annotation, ...]
    languages: tuple[str, ...]
    layout_direction: str
    max_nodes: int
    max_edges: int
    preset: str
    arrangement: tuple[str, ...] | None = None   # canvas grouping, outer first; None: the default (level lanes)

    def to_json(self) -> dict:
        return {
            "schemaVersion": 1, "id": self.id, "title": self.title,
            "scope": {"primaryGraphs": list(self.primary_graphs), "contextGraphs": list(self.context_graphs)},
            "selection": {"entities": list(self.entities), "excludedEntities": list(self.excluded),
                          "levels": list(self.levels), "relationKinds": list(self.relation_kinds),
                          "relationTypeIris": list(self.relation_types),
                          "expansion": {"hops": self.hops, "direction": self.direction}},
            "groups": [{"id": g.id, "title": g.title, "members": list(g.members), "collapsed": g.collapsed}
                       for g in self.groups],
            "summaries": [{"id": s.id, "steps": [{"relationshipId": st.relationship_id, "direction": st.direction}
                                                 for st in s.steps],
                           **({"label": s.label} if s.label is not None else {}),
                           "hideIntermediateEntities": s.hide_intermediate} for s in self.summaries],
            "annotations": [{"id": a.id, "text": a.text, "target": {"kind": a.target_kind, "id": a.target_id}}
                            for a in self.annotations],
            "presentation": {"languages": list(self.languages), "direction": self.layout_direction,
                             "maxNodes": self.max_nodes, "maxEdges": self.max_edges, "preset": self.preset,
                             # written only when set, so a spec without it keeps its digest
                             **({"arrangement": list(self.arrangement)} if self.arrangement is not None else {})},
        }


@dataclass(frozen=True)
class Checked:
    spec: ViewSpec
    scope: Scope
    diagnostics: tuple[Issue, ...]     # tolerant mode only; strict raises instead


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _where(path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "(root)"


def parse(data: dict) -> ViewSpec:
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        raise SpecError([Issue("schema", e.message[:300], _where(e.absolute_path)) for e in errors])
    sel = data["selection"]
    spec = ViewSpec(
        id=data["id"], title=data["title"],
        primary_graphs=tuple(data["scope"]["primaryGraphs"]), context_graphs=tuple(data["scope"]["contextGraphs"]),
        entities=tuple(sel["entities"]), excluded=tuple(sel["excludedEntities"]), levels=tuple(sel["levels"]),
        relation_kinds=tuple(sel["relationKinds"]), relation_types=tuple(sel["relationTypeIris"]),
        hops=sel["expansion"]["hops"], direction=sel["expansion"]["direction"],
        groups=tuple(Group(g["id"], g["title"], tuple(g["members"]), g["collapsed"]) for g in data["groups"]),
        summaries=tuple(Summary(s["id"], tuple(Step(st["relationshipId"], st["direction"]) for st in s["steps"]),
                                s.get("label"), s["hideIntermediateEntities"]) for s in data["summaries"]),
        annotations=tuple(Annotation(a["id"], a["text"], a["target"]["kind"], a["target"]["id"])
                          for a in data["annotations"]),
        languages=tuple(data["presentation"]["languages"]), layout_direction=data["presentation"]["direction"],
        max_nodes=data["presentation"]["maxNodes"], max_edges=data["presentation"]["maxEdges"],
        preset=data["presentation"]["preset"],
        arrangement=tuple(a) if (a := data["presentation"].get("arrangement")) is not None else None)
    issues = _structure(spec)
    if issues:
        raise SpecError(issues)
    return spec


def _structure(spec: ViewSpec) -> list[Issue]:
    issues = []
    excluded = set(spec.excluded)
    for i, e in enumerate(spec.entities):
        if e in excluded:
            issues.append(Issue("seed-excluded", f"{e} is both a seed and excluded", f"selection.entities[{i}]"))
    if not spec.entities and not spec.levels:
        issues.append(Issue("empty-selection", "no seed entities and no level: a view never means the whole dataset",
                            "selection"))
    for kind, items in (("groups", spec.groups), ("summaries", spec.summaries), ("annotations", spec.annotations)):
        seen = set()
        for i, item in enumerate(items):
            if item.id in seen:
                issues.append(Issue("duplicate-id", f"id {item.id!r} repeats", f"{kind}[{i}].id"))
            seen.add(item.id)
    owner: dict[str, str] = {}
    for gi, g in enumerate(spec.groups):
        for mi, m in enumerate(g.members):
            if m in owner:
                issues.append(Issue("multiple-groups", f"{m} is already in group {owner[m]!r}",
                                    f"groups[{gi}].members[{mi}]"))
            owner.setdefault(m, g.id)
    return issues


def check(spec: ViewSpec, cfg: Config, graphs: Sequence[GraphEntry], model: SourceModel,
          mode: str = "strict") -> Checked:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    scope = resolve(cfg, graphs, spec.primary_graphs, spec.context_graphs)   # a configuration fault in any mode
    levels = set(Preset.load(cfg.preset_path).level_ids)
    bad_levels = [Issue("unknown-level", f"level {lv!r} is not in the preset", f"selection.levels[{i}]")
                  for i, lv in enumerate(spec.levels) if lv not in levels]
    if bad_levels:
        raise SpecError(bad_levels)
    issues = _references(spec, model)
    if issues and mode == "strict":
        raise SpecError(issues)
    return Checked(spec=spec, scope=scope, diagnostics=tuple(issues))


def _references(spec: ViewSpec, model: SourceModel) -> list[Issue]:
    issues = []
    known = model.entities
    for i, e in enumerate(spec.entities):
        if e not in known:
            issues.append(Issue("unknown-entity", f"{e} is not in the scope", f"selection.entities[{i}]"))
    for gi, g in enumerate(spec.groups):
        for mi, m in enumerate(g.members):
            if m not in known:
                issues.append(Issue("unknown-entity", f"{m} is not in the scope", f"groups[{gi}].members[{mi}]"))
    rels = {r.id: r for r in model.relationships}
    for si, s in enumerate(spec.summaries):
        ends = []
        for ti, st in enumerate(s.steps):
            r = rels.get(st.relationship_id)
            if r is None:
                issues.append(Issue("unknown-relationship", f"relationship {st.relationship_id} is not in the scope",
                                    f"summaries[{si}].steps[{ti}]"))
                ends = None
                continue
            if ends is not None:
                ends.append((r.source, r.target) if st.direction == "forward" else (r.target, r.source))
        if ends and any(a[1] != b[0] for a, b in zip(ends, ends[1:])):
            issues.append(Issue("discontiguous-summary", "the steps are not contiguous in the stated directions",
                                f"summaries[{si}].steps"))
        elif ends:
            missing = sorted({e for pair in ends for e in pair if e is None or e not in known}, key=str)
            if missing:
                issues.append(Issue("unknown-entity", f"the path passes through {missing[0]}, which is not in the scope",
                                    f"summaries[{si}].steps"))
    targets = {"entity": set(known), "group": {g.id for g in spec.groups}, "summary": {s.id for s in spec.summaries}}
    for ai, a in enumerate(spec.annotations):
        if a.target_id not in targets[a.target_kind]:
            issues.append(Issue("unknown-target", f"{a.target_kind} {a.target_id!r} does not exist",
                                f"annotations[{ai}].target"))
    return issues
