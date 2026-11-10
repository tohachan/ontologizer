"""Saved views: owner-relative JSON artifacts with a revision (docs/visualizer.md, Persistence and refresh).

An artifact holds the ViewSpec, the layout, the source manifest of the compilation it was saved
from, and a baseline: what the view showed then (entity labels and statuses, relationships). A
refresh compares a new compilation with the baseline, which fingerprints alone could not do.

Writes are atomic (a temporary file in the same directory, then a rename) and checked against the
revision the writer last saw, under a lock, so a stale save is a conflict instead of an overwrite.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ontologizer.core.views.models import SourceModel
from ontologizer.core.views.project import DiagramModel
from ontologizer.core.views.reader import choose_label
from ontologizer.core.views.spec import SpecError, ViewSpec, parse

ARTIFACT_VERSION = 1
SUFFIX = ".view.json"
VIEW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
VISUAL_ID = re.compile(r"[A-Za-z]+:[A-Za-z0-9_.:-]{1,80}")
MAX_POSITIONS = 2000
MAX_BYTES = 8 << 20


class StorageError(ValueError):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.details = code, details


@dataclass(frozen=True)
class Stored:
    revision: int
    data: dict


def baseline(diagram: DiagramModel, model: SourceModel, languages) -> dict:
    """What the view shows, by identity: entities with label and status, relationships with status."""
    iris = sorted({m for n in diagram.nodes if n.kind in ("concept", "aggregate", "area") for m in n.members})
    rels = sorted({r for e in diagram.edges for r in e.relationships})
    by_id = {r.id: r for r in model.relationships}
    return {
        "entities": {i: {"label": choose_label(model.entities[i], languages), "status": model.entities[i].status}
                     for i in iris if i in model.entities},
        "relationships": {r: {"status": by_id[r].status, "label": by_id[r].label, "source": by_id[r].source,
                              "target": by_id[r].target, "type": by_id[r].type} for r in rels if r in by_id},
    }


def diff(saved: dict, now: dict, model: SourceModel, saved_fingerprints: dict, fingerprints: dict) -> dict:
    """What changed for this view since it was saved. An entity or relationship that is gone from the
    source is `removed`; one still there but no longer shown is `notShown`."""
    se, ne = saved.get("entities", {}), now["entities"]
    sr, nr = saved.get("relationships", {}), now["relationships"]
    known_rels = {r.id for r in model.relationships}
    return {
        "addedEntities": sorted(set(ne) - set(se)),
        "removedEntities": sorted(i for i in set(se) - set(ne) if i not in model.entities),
        "notShownEntities": sorted(i for i in set(se) - set(ne) if i in model.entities),
        "changedLabels": sorted(i for i in set(se) & set(ne) if se[i].get("label") != ne[i]["label"]),
        "changedStatus": sorted(i for i in set(se) & set(ne) if se[i].get("status") != ne[i]["status"]),
        "addedRelationships": sorted(set(nr) - set(sr)),
        "removedRelationships": sorted(r for r in set(sr) - set(nr) if r not in known_rels),
        "notShownRelationships": sorted(r for r in set(sr) - set(nr) if r in known_rels),
        "changedRelationshipStatus": sorted(r for r in set(sr) & set(nr) if sr[r].get("status") != nr[r]["status"]),
        "changedRelationshipLabels": sorted(r for r in set(sr) & set(nr) if sr[r].get("label") != nr[r]["label"]),
        "changedGraphs": sorted(g for g in set(saved_fingerprints) | set(fingerprints)
                                if saved_fingerprints.get(g) != fingerprints.get(g)),
    }


def check_layout(layout) -> dict:
    if not isinstance(layout, dict) or set(layout) - {"positions", "pinned", "viewport", "engine"}:
        raise StorageError("invalid-layout", "layout has positions, pinned, viewport and engine only")
    pos = layout.get("positions", {})
    if not isinstance(pos, dict) or len(pos) > MAX_POSITIONS:
        raise StorageError("invalid-layout", f"positions must be an object of at most {MAX_POSITIONS} entries")
    for k, v in pos.items():
        if not VISUAL_ID.fullmatch(k) or not isinstance(v, dict) or set(v) != {"x", "y"} or \
                not all(isinstance(v[c], (int, float)) and math.isfinite(v[c]) for c in "xy"):
            raise StorageError("invalid-layout", f"bad position {k!r}")
    pinned = layout.get("pinned", [])
    if not isinstance(pinned, list) or not all(isinstance(p, str) and p in pos for p in pinned):
        raise StorageError("invalid-layout", "pinned lists positioned visual IDs")
    vp = layout.get("viewport")
    if vp is not None and (not isinstance(vp, dict) or set(vp) != {"x", "y", "zoom"} or
                           not all(isinstance(vp[c], (int, float)) and math.isfinite(vp[c]) for c in vp)):
        raise StorageError("invalid-layout", "viewport is {x, y, zoom}")
    engine = layout.get("engine", "")
    if not isinstance(engine, str) or len(engine) > 64:
        raise StorageError("invalid-layout", "engine is a short string")
    return {"positions": pos, "pinned": sorted(set(pinned)), "viewport": vp, "engine": engine}


class ViewStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, view_id: str) -> Path:
        if not isinstance(view_id, str) or not VIEW_ID.fullmatch(view_id):
            raise StorageError("bad-view-id", "a view id is 1 to 64 letters, digits, '.', '_' or '-'")
        return self.dir / f"{view_id}{SUFFIX}"

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".views.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def get(self, view_id: str) -> Stored:
        p = self._path(view_id)
        if not p.is_file():
            raise StorageError("not-found", f"no saved view {view_id!r}")
        if p.stat().st_size > MAX_BYTES:
            raise StorageError("unreadable", "the saved view is too large")
        try:
            data = json.loads(p.read_text())
        except (ValueError, UnicodeDecodeError):
            raise StorageError("unreadable", f"{p.name} is not JSON") from None
        if not isinstance(data, dict) or data.get("artifactVersion") != ARTIFACT_VERSION:
            raise StorageError("unsupported-version", f"{p.name} has an unsupported artifact version")
        return Stored(int(data.get("revision", 0)), data)

    def list(self) -> list[dict]:
        out = []
        if not self.dir.is_dir():
            return out
        for p in sorted(self.dir.glob(f"*{SUFFIX}")):
            vid = p.name[: -len(SUFFIX)]
            try:
                s = self.get(vid)
                out.append({"id": vid, "title": s.data["spec"].get("title"), "revision": s.revision})
            except (StorageError, KeyError, AttributeError) as e:
                out.append({"id": vid, "title": None, "revision": None, "error": getattr(e, "code", "unreadable")})
        return out

    def put(self, spec: ViewSpec, layout: dict, manifest: dict, base: dict, expected: int | None) -> int:
        path = self._path(spec.id)
        layout = check_layout(layout)
        with self._locked():
            current = self.get(spec.id).revision if path.is_file() else None
            if current != expected:
                raise StorageError("save-conflict",
                                   "the view was saved since you opened it" if current is not None
                                   else "the view does not exist; save it as new",
                                   {"currentRevision": current})
            revision = (current or 0) + 1
            data = {"artifactVersion": ARTIFACT_VERSION, "revision": revision, "spec": spec.to_json(),
                    "layout": layout, "manifest": manifest, "baseline": base}
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{spec.id}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            return revision


def spec_of(stored: Stored) -> ViewSpec:
    try:
        return parse(stored.data["spec"])
    except (SpecError, KeyError) as e:
        raise StorageError("unreadable", f"the saved spec is invalid: {e}") from None
