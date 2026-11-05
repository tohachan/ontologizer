"""Stage 0: the frame (`frame.md`) and recorded gates (`gates.yaml`).

The frame is markdown with a YAML header. The header is what code reads: the
domains, the reuse list, the corpus by architectural family, the overlay
target, the competency questions and the preset; optionally the variation
dimensions (the second axis of comparison, asked of every extraction) and
design targets (requirement profiles: competency questions tagged with
`target:` that the run report answers per project as a fit matrix). The body explains them to
people and must have the sections of `templates/frame.md`.

A gate is the owner's approval of a file at a given hash. Stages that need it
refuse to start when it is missing or the file changed after the approval.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from ontologizer.core.config import Config

SECTIONS = ("Domains", "Reuse", "Corpus", "Competency questions", "Levels", "Overlay", "Out of scope")
HEADER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CQ_LEVELS = ("domain", "task", "application", "variation", "top")
FIT = ("full", "partial", "none")


class FrameError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def frame_path(cfg: Config) -> Path:
    return cfg.root / ((cfg.data.get("process") or {}).get("frame") or "frame.md")


def parse(text: str) -> tuple[dict, str]:
    m = HEADER.match(text)
    if not m:
        raise FrameError(["frame: YAML header between `---` lines required at the top"])
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def check(text: str, preset_names: list[str] | None = None) -> list[str]:
    try:
        h, body = parse(text)
    except (FrameError, yaml.YAMLError) as e:
        return getattr(e, "errors", [str(e)])
    errs: list[str] = []
    if not h.get("study"):
        errs.append("study: required")
    if not h.get("preset"):
        errs.append("preset: required")
    elif preset_names is not None and h["preset"] not in preset_names:
        errs.append(f"preset: one of {preset_names}")
    doms = h.get("domains") or []
    if not doms:
        errs.append("domains: at least one")
    for i, d in enumerate(doms):
        if not SLUG.match(str(d.get("id", ""))) or not d.get("label") or not d.get("boundary"):
            errs.append(f"domains[{i}]: id (slug), label and boundary required")
    for i, r in enumerate(h.get("reuse") or []):
        if not r.get("name") or not r.get("use"):
            errs.append(f"reuse[{i}]: name and use required")
    fams = h.get("families") or {}
    if len(fams) < 2:
        errs.append("families: at least two architectural families (support needs two)")
    seen: dict[str, str] = {}
    for fid, f in fams.items():
        if not SLUG.match(str(fid)):
            errs.append(f"families.{fid}: slug id required")
        if not (f or {}).get("projects"):
            errs.append(f"families.{fid}.projects: at least one")
        for p in (f or {}).get("projects") or []:
            if p in seen:
                errs.append(f"project {p}: in families {seen[p]} and {fid}; one family each")
            seen[p] = fid
    for p in h.get("overlay") or []:
        if p in seen:
            errs.append(f"overlay {p}: also in the corpus (family {seen[p]}); the overlay target is never read "
                        f"while building the shared levels")
    dim_ids = set()
    for i, d in enumerate(h.get("dimensions") or []):
        if not SLUG.match(str(d.get("id", ""))) or not d.get("label") or not d.get("definition"):
            errs.append(f"dimensions[{i}]: id (slug), label and definition required")
        if d.get("id") in dim_ids:
            errs.append(f"dimensions[{i}]: duplicate id {d.get('id')}")
        dim_ids.add(d.get("id"))
        vals = d.get("values") or []
        if len(vals) < 2:
            errs.append(f"dimensions[{i}].values: at least two")
        if len({v.get("id") for v in vals}) != len(vals):
            errs.append(f"dimensions[{i}].values: duplicate id")
        for j, v in enumerate(vals):
            if not SLUG.match(str(v.get("id", ""))) or not v.get("definition"):
                errs.append(f"dimensions[{i}].values[{j}]: id (slug) and definition required")
    tids = set()
    for i, t in enumerate(h.get("targets") or []):
        if not SLUG.match(str(t.get("id", ""))) or not t.get("label") or not t.get("description"):
            errs.append(f"targets[{i}]: id (slug), label and description required")
        tids.add(t.get("id"))
    cqs = h.get("competency_questions") or []
    if not cqs:
        errs.append("competency_questions: at least one")
    ids = set()
    for i, q in enumerate(cqs):
        if not SLUG.match(str(q.get("id", ""))) or not q.get("question"):
            errs.append(f"competency_questions[{i}]: id (slug) and question required")
        if q.get("id") in ids:
            errs.append(f"competency_questions[{i}]: duplicate id {q.get('id')}")
        ids.add(q.get("id"))
        if q.get("level") not in CQ_LEVELS:
            errs.append(f"competency_questions[{i}].level: one of {CQ_LEVELS}")
        if q.get("target") is not None and q["target"] not in tids:
            errs.append(f"competency_questions[{i}].target: {q['target']!r} is not a target of the frame")
    for t in tids - {q.get("target") for q in cqs}:
        errs.append(f"targets.{t}: no competency question carries `target: {t}`")
    if "out_of_scope" not in h:
        errs.append("out_of_scope: required (may be an empty list)")
    heads = {m.strip() for m in re.findall(r"^##\s+(.+)$", body, re.M)}
    for s in SECTIONS:
        if s not in heads:
            errs.append(f"section `## {s}` missing")
        else:
            part = re.split(rf"^##\s+{re.escape(s)}\s*$", body, flags=re.M)[1]
            part = re.split(r"^##\s+", part, flags=re.M)[0]
            if not part.strip():
                errs.append(f"section `## {s}` is empty")
    return errs


class Frame:
    def __init__(self, header: dict, body: str, path: Path | None = None):
        self.h, self.body, self.path = header, body, path

    @classmethod
    def load(cls, cfg: Config) -> "Frame":
        p = frame_path(cfg)
        if not p.is_file():
            raise FrameError([f"no frame at {p}; start one with `ontologizer frame init`"])
        errs = check(p.read_text())
        if errs:
            raise FrameError(errs)
        h, body = parse(p.read_text())
        return cls(h, body, p)

    @property
    def families(self) -> dict[str, dict]:
        return self.h.get("families") or {}

    @property
    def projects(self) -> list[str]:
        return [p for f in self.families.values() for p in f["projects"]]

    def family_of(self, project: str) -> str | None:
        return next((fid for fid, f in self.families.items() if project in f["projects"]), None)

    @property
    def overlay(self) -> list[str]:
        return list(self.h.get("overlay") or [])

    @property
    def questions(self) -> list[dict]:
        return list(self.h.get("competency_questions") or [])

    @property
    def dimensions(self) -> list[dict]:
        return list(self.h.get("dimensions") or [])

    @property
    def targets(self) -> list[dict]:
        return list(self.h.get("targets") or [])


def sync_variation(cfg: Config, frame: "Frame") -> Path:
    """Write the frame's dimensions into the shared variation file (`paths.variation`, graph
    `shared.variation`). Values the file has beyond the frame (accepted proposals) are kept."""
    import json
    graph = cfg.shared.get("variation")
    if not graph:
        raise FrameError([f"no `shared.variation` in {cfg.root / 'ontologizer.yaml'}"])
    d = cfg.path("variation")
    hits = [p for p in sorted(d.glob("*.json")) if json.loads(p.read_text()).get("graph") == graph]
    p = hits[0] if hits else d / f"{graph.split('/')[0]}.json"
    doc = json.loads(p.read_text()) if p.is_file() else {"graph": graph, "concepts": []}
    old = {x["id"]: x for x in doc.get("dimensions", [])}
    dims = []
    for fd in frame.dimensions:
        vals = [{k: v[k] for k in ("id", "label", "definition") if k in v} for v in fd["values"]]
        have = {v["id"] for v in vals}
        vals += [v for v in (old.get(fd["id"]) or {}).get("values", []) if v["id"] not in have]
        dims.append({"id": fd["id"], "label": fd["label"], "definition": fd["definition"], "values": vals})
    dims += [x for i, x in old.items() if i not in {d["id"] for d in dims}]
    doc["dimensions"] = dims
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return p


# ---- gates --------------------------------------------------------------------
def _gates_path(cfg: Config) -> Path:
    return cfg.root / "gates.yaml"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def record(cfg: Config, entry: dict) -> None:
    """Append a non-approval record (e.g. an unreviewed release) to gates.yaml."""
    _gates_path(cfg).write_text(yaml.safe_dump(gates(cfg) + [entry], sort_keys=False, allow_unicode=True))


def gates(cfg: Config) -> list[dict]:
    p = _gates_path(cfg)
    return (yaml.safe_load(p.read_text()) or []) if p.is_file() else []


def approve(cfg: Config, gate: str, file: Path, by: str, at: str, note: str | None = None,
            covers: dict[str, str] | None = None) -> dict:
    entry = {"gate": gate, "file": str(file.relative_to(cfg.root)), "sha256": sha256(file), "by": by, "at": at}
    if note:
        entry["note"] = note
    if covers:
        entry["covers"] = covers
    all_ = gates(cfg) + [entry]
    _gates_path(cfg).write_text(yaml.safe_dump(all_, sort_keys=False, allow_unicode=True))
    return entry


def _gates_for(cfg: Config, gate: str, file: Path) -> list[dict]:
    """The approvals that answer for `file`. A frame kept by another owner (an overlay target reads
    the study's frame, stage 9) is approved in the gates.yaml beside it, never in the reader's."""
    if gate == "frame" and file.resolve().parent != cfg.root.resolve():
        p = file.resolve().parent / "gates.yaml"
        return (yaml.safe_load(p.read_text()) or []) if p.is_file() else []
    return gates(cfg)


def gate_status(cfg: Config, gate: str, file: Path) -> tuple[bool, str]:
    """(passed, why). The latest approval of `gate` must match the file's current hash."""
    hits = [g for g in _gates_for(cfg, gate, file) if g.get("gate") == gate]
    if not hits:
        return False, f"gate {gate!r}: no approval recorded in gates.yaml (`ontologizer gate approve {gate} --by NAME`)"
    last = hits[-1]
    if not file.is_file():
        return False, f"gate {gate!r}: {file} missing"
    if last["sha256"] != sha256(file):
        return False, f"gate {gate!r}: {file.name} changed after the approval by {last['by']} at {last['at']}; approve again"
    return True, f"gate {gate!r}: approved by {last['by']} at {last['at']}"


def require_frame(cfg: Config) -> Frame:
    fr = Frame.load(cfg)
    ok, why = gate_status(cfg, "frame", fr.path)
    if not ok:
        raise FrameError([why])
    return fr
