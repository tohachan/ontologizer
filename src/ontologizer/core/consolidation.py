"""Apply a consolidation decision file to the shared-level and application JSONs.

Usage: ontologizer consolidate DECISIONS.yaml  (shared-level and application files from `paths:` of the config)

- adds the new shared domain/task concepts (evidence = supporting project concepts);
- adds domain relations;
- rebinds project `candidate` mappings whose label was consolidated to the new IRI;
- adds mappings for supporting/attached project concepts that lacked one.
Idempotent: re-running on its own output changes nothing. Ingest afterwards.

With a frame (docs/process.md §5) every new shared concept also needs a
`reason`, and its supporting projects must come from at least two
architectural families of the frame; the overlay target may not support it.
The families and the reason are recorded in the concept's `support` field.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import yaml

NOTE = "bound in step 3 consolidation"


def load(p):
    return json.loads(Path(p).read_text())


def save(p, d):
    Path(p).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")


def check_independence(dec: dict, frame, need: int = 2) -> list[str]:
    """Stage 5 criterion: support from ≥2 architectural families, a reason, no overlay target."""
    errs = []
    if not dec.get("model"):
        errs.append("`model:` required: the id of the model that made the decisions (stage 5 runs on Opus)")
    for key in ("new_domain", "new_task"):
        for n in dec.get(key, []):
            projects = {r.split(":")[0] for r in n["support"]}
            bad = sorted(projects & set(frame.overlay))
            if bad:
                errs.append(f"{n['id']}: supported by the overlay target {bad}; it is never read while building "
                            f"the shared levels")
            fams = {frame.family_of(p) for p in projects}
            if None in fams:
                errs.append(f"{n['id']}: support from projects outside the frame's corpus "
                            f"{sorted(p for p in projects if frame.family_of(p) is None)}")
            if len(fams - {None}) < need:
                errs.append(f"{n['id']}: support from {len(fams - {None})} architectural famil(y/ies); {need} required")
            if not str(n.get("reason", "")).strip():
                errs.append(f"{n['id']}: `reason` required")
    return errs


def apply(decision_file: str | Path, seeds: dict[str, Path], extractions_dir: Path, frame=None,
          min_families: int = 2) -> int:
    """`seeds`: level → the JSON file of that shared level ("domain", "task")."""
    dec = yaml.safe_load(Path(decision_file).read_text())
    if frame is not None:
        errs = check_independence(dec, frame, min_families)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 1
    shared = {k: load(v) for k, v in seeds.items()}
    extractions = {load(f)["project"]: (f, load(f)) for f in sorted(glob.glob(str(extractions_dir / "*.json")))}
    if frame is not None:   # the overlay target is never read here
        extractions = {p: v for p, v in extractions.items() if p not in frame.overlay}
    concepts = {(p, c["id"]): c for p, (_, d) in extractions.items() for c in d["concepts"]}

    errors = []
    label_to_target: dict[str, str] = {}
    for level, key in (("domain", "new_domain"), ("task", "new_task")):
        doc = shared[level]
        existing = {c["id"]: c for c in doc["concepts"]}
        for n in dec.get(key, []):
            for ref in n["support"] + n.get("attach_unmapped", {}).get("ids", []):
                if tuple(ref.split(":")) not in concepts:
                    errors.append(f"{n['id']}: unknown project concept {ref}")
            c = {"id": n["id"], "label": n["label"], "alt_labels": n.get("alt", []), "definition": n["def"],
                 "level": level, "upper_category": n["cat"], "status": "hypothesis",
                 "note": f"consolidated in step 3 from {len({r.split(':')[0] for r in n['support']})} projects",
                 "evidence": [{"path": f"projects/{r.split(':')[0]}#{r.split(':')[1]}", "kind": "graph"}
                              for r in n["support"]],
                 "maps_to": [{"target": m["target"], "kind": m["kind"], "confidence": "medium"} for m in n.get("maps", [])]}
            if frame is not None:
                projs = sorted({r.split(":")[0] for r in n["support"]})
                c["support"] = {"projects": projs, "families": sorted({frame.family_of(p) for p in projs}),
                                "reason": n["reason"], "decision": Path(decision_file).name}
            existing[n["id"]] = c
            for lab in n.get("candidates", []):
                label_to_target[lab.lower()] = f"{level}:{n['id']}"
        for ref, why in dec.get("weak", {}).items():
            lv, cid = ref.split(":")
            if lv == level and cid in existing:
                existing[cid]["note"] = f"weak support after step 2: {why}"
        doc["concepts"] = sorted(existing.values(), key=lambda c: c["id"])

    rels = []
    for frm, typ, to, label, projs in dec.get("relations", []):
        rels.append({"from": frm, "to": to, "type": typ, "label": label, "status": "hypothesis",
                     "evidence": [{"path": f"projects/{p}", "kind": "graph"} for p in projs]})
    shared["domain"]["relations"] = rels
    task_ids = {c["id"] for c in shared["task"]["concepts"]}
    domain_ids = {c["id"] for c in shared["domain"]["concepts"]}
    for r in rels:
        for end in (r["from"], r["to"]):
            if end not in domain_ids:
                errors.append(f"relation end {end} not a domain concept")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    # rebind candidates
    rebound = added = 0
    for p, (f, d) in extractions.items():
        for c in d["concepts"]:
            for m in c.get("maps_to", []):
                tgt = label_to_target.get(str(m.get("candidate", "")).lower())
                if tgt:
                    if tgt.startswith("task:") and m["kind"] == "roleOf":
                        m["kind"] = "realization"
                    m.pop("candidate", None)
                    m.pop("candidate_level", None)
                    m["target"] = tgt
                    m["note"] = (m.get("note", "") + f" ({NOTE})").strip()
                    rebound += 1
    # supporting concepts and attachments without a mapping to their shared concept
    for level, key in (("domain", "new_domain"), ("task", "new_task")):
        for n in dec.get(key, []):
            tgt = f"{level}:{n['id']}"
            att = n.get("attach_unmapped", {})
            for ref in n["support"] + att.get("ids", []):
                p, cid = ref.split(":")
                c = concepts[(p, cid)]
                if any(m.get("target") == tgt for m in c.get("maps_to", [])):
                    continue
                kind = att.get("kind") if ref in att.get("ids", []) else ("realization" if level == "task" else "specialization")
                if level == "task" and kind == "roleOf":
                    kind = "realization"
                c.setdefault("maps_to", []).append({"target": tgt, "kind": kind, "confidence": "medium",
                                                    "note": f"added in step 3 consolidation as support for {tgt}"})
                added += 1
    for lv in seeds:
        save(seeds[lv], shared[lv])
    for p, (f, d) in extractions.items():
        save(f, d)
    left = sum(1 for (_, d) in extractions.values() for c in d["concepts"] for m in c.get("maps_to", []) if m.get("candidate"))
    print(f"domain concepts {len(domain_ids)}, task concepts {len(task_ids)}, relations {len(rels)}; "
          f"rebound {rebound} candidates, added {added} mappings, {left} single-project candidates left")
    return 0

