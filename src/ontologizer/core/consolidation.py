"""Apply a consolidation decision file to the shared-level and application JSONs.

Usage: ontologizer consolidate DECISIONS.yaml  (shared-level and application files from `paths:` of the config)

- adds the new shared domain/task concepts (evidence = supporting project concepts); a concept
  decided on another level than before moves there, and mappings to it follow;
- removes what `removed:` names ({ref, reason, replaced_by}), with its relations and the mappings to it;
- adds relations to those of earlier decisions, on the level of their `from` end. An end is a concept
  id, or `domain:<id>` / `task:<id>` when the id exists on both levels. `remove_relations:`
  ({relation: [from, type, to], reason}) takes relations out;
- rebinds project `candidate` mappings whose label was consolidated to the new IRI;
- adds mappings for supporting/attached project concepts that lacked one, as `support_kinds:` says
  ({ref: kind or {kind, overlap, differs}}; by default specialization, on the task level realization),
  and removes the ones this step added earlier for a concept no longer in the support;
- `settled:` ({concept, target, reason}) records why a partial or disputed project concept need not make
  its shared concept disputed, and `disputed:` ({ref, reason}) declares a dispute no mapping shows; a later
  `settled:` ({target, reason}) ends one. `review apply` acts on them (`shared.disputes`).
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
ADDED = "added in step 3 consolidation as support for"


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
    errors += _check_removals(dec, shared)
    errors += _check_disputes(dec, shared, concepts)
    moved = _moves(dec, shared)
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

    gone = {x["ref"] for x in dec.get("removed") or []}
    for level, doc in shared.items():
        doc["concepts"] = [c for c in doc["concepts"] if f"{level}:{c['id']}" not in gone
                           and (c["id"], level) not in moved]
    ids = {level: {c["id"] for c in doc["concepts"]} for level, doc in shared.items()}
    errors += _relations(dec, shared, ids, gone, moved)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    task_ids, domain_ids = ids["task"], ids["domain"]
    rels = [r for doc in shared.values() for r in doc.get("relations", [])]
    _rebind_shared(shared, gone, moved)

    # rebind candidates
    rebound = added = unbound = 0
    for p, (f, d) in extractions.items():
        for c in d["concepts"]:
            c_maps = c.get("maps_to", [])
            kept = [m for m in c_maps if m.get("target") not in gone]
            unbound += len(c_maps) - len(kept)
            if len(kept) != len(c_maps):
                c["maps_to"] = kept
            for m in kept:
                _follow_move(m, moved)
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
            refs = n["support"] + att.get("ids", [])
            for (p, cid), c in concepts.items():    # a concept taken out of the support loses what this step gave it
                if f"{p}:{cid}" in refs:
                    continue
                maps = c.get("maps_to", [])
                kept = [m for m in maps if not (m.get("target") == tgt and str(m.get("note", "")).startswith(ADDED))]
                if len(kept) != len(maps):
                    unbound += len(maps) - len(kept)
                    c["maps_to"] = kept
            for ref in refs:
                p, cid = ref.split(":")
                c = concepts[(p, cid)]
                how = (n.get("support_kinds") or {}).get(ref)
                how = {"kind": how} if isinstance(how, str) else (how or {})
                have = next((m for m in c.get("maps_to", []) if m.get("target") == tgt), None)
                if have is not None:
                    if how and str(have.get("note", "")).startswith(ADDED):
                        have.update(how)
                    continue
                kind = att.get("kind") if ref in att.get("ids", []) else ("realization" if level == "task" else "specialization")
                if level == "task" and kind == "roleOf":
                    kind = "realization"
                c.setdefault("maps_to", []).append({"target": tgt, "kind": kind, "confidence": "medium",
                                                    "note": f"{ADDED} {tgt}", **how})
                added += 1
    for lv in seeds:
        save(seeds[lv], shared[lv])
    for p, (f, d) in extractions.items():
        save(f, d)
    left = sum(1 for (_, d) in extractions.values() for c in d["concepts"] for m in c.get("maps_to", []) if m.get("candidate"))
    print(f"domain concepts {len(domain_ids)}, task concepts {len(task_ids)}, relations {len(rels)}; "
          f"rebound {rebound} candidates, added {added} mappings, {left} single-project candidates left"
          + (f"; removed {len(gone)} concepts, {unbound} mappings" if gone or unbound else "")
          + (f"; moved {len(moved)} concepts across levels" if moved else ""))
    return 0


def _check_removals(dec: dict, shared: dict) -> list[str]:
    errs = []
    decided = {f"{lv}:{n['id']}" for lv, key in (("domain", "new_domain"), ("task", "new_task"))
               for n in dec.get(key, [])}
    for x in dec.get("removed") or []:
        ref = x.get("ref", "")
        if not str(x.get("reason", "")).strip():
            errs.append(f"removed {ref}: `reason` required")
        if ref in decided:
            errs.append(f"removed {ref}: also decided as a concept in this file")
        lv, _, cid = ref.partition(":")
        if lv not in shared:
            errs.append(f"removed {ref}: `domain:<id>` or `task:<id>` required")
    both = {n["id"] for n in dec.get("new_domain", [])} & {n["id"] for n in dec.get("new_task", [])}
    errs += [f"{cid}: decided on both levels" for cid in sorted(both)]
    for x in dec.get("remove_relations") or []:
        if len(x.get("relation") or []) != 3 or not str(x.get("reason", "")).strip():
            errs.append(f"remove_relations {x.get('relation')}: [from, type, to] and `reason` required")
    return errs


def _check_disputes(dec: dict, shared: dict, concepts: dict) -> list[str]:
    errs = []
    decided = {f"{lv}:{n['id']}" for lv, key in (("domain", "new_domain"), ("task", "new_task"))
               for n in dec.get(key, [])}
    known = decided | {f"{lv}:{c['id']}" for lv, doc in shared.items() for c in doc["concepts"]}
    for x in dec.get("disputed") or []:
        if not str(x.get("reason", "")).strip():
            errs.append(f"disputed {x.get('ref')}: `reason` required")
        if x.get("ref") not in known:
            errs.append(f"disputed {x.get('ref')}: not a shared concept")
    for x in dec.get("settled") or []:
        if not str(x.get("reason", "")).strip():
            errs.append(f"settled {x.get('concept') or x.get('target')}: `reason` required")
        if x.get("target") not in known:
            errs.append(f"settled {x.get('target')}: not a shared concept")
        if x.get("concept") and tuple(x["concept"].split(":")) not in concepts:
            errs.append(f"settled {x['concept']}: unknown project concept")
    return errs


def _moves(dec: dict, shared: dict) -> set[tuple[str, str]]:
    """(id, level it leaves): a concept decided on a level while the other level still holds it."""
    out = set()
    for level, key, other in (("domain", "new_domain", "task"), ("task", "new_task", "domain")):
        there = {c["id"] for c in shared[other]["concepts"]}
        for n in dec.get(key, []):
            if n["id"] in there:
                out.add((n["id"], other))
    return out


def _follow_move(m: dict, moved: set[tuple[str, str]]) -> None:
    lv, _, cid = str(m.get("target", "")).partition(":")
    if (cid, lv) in moved:
        new = "task" if lv == "domain" else "domain"
        m["target"] = f"{new}:{cid}"
        if new == "task" and m.get("kind") == "roleOf":
            m["kind"] = "realization"


def _end(x: str, ids: dict[str, set[str]]) -> tuple[str | None, str]:
    """(level, id) of a relation end, or (None, x) when it names no concept."""
    lv, _, cid = str(x).partition(":")
    if cid and lv in ids:
        return (lv, cid) if cid in ids[lv] else (None, x)
    for level in ("domain", "task"):
        if x in ids.get(level, ()):
            return level, x
    return None, x


def _relations(dec: dict, shared: dict, ids: dict[str, set[str]], gone: set[str], moved: set) -> list[str]:
    """Relations of this decision join those of earlier ones. Each lives on the level of its `from` end."""
    errors, have = [], {}
    drop = {tuple(x["relation"]) for x in dec.get("remove_relations") or []}
    for level, doc in shared.items():
        for r in doc.get("relations", []):
            frm, to = r["from"], r["to"]
            flv, fid = _end(f"{level}:{frm}" if ":" not in frm else frm, ids)
            tlv, tid = _end(f"{level}:{to}" if ":" not in to else to, ids)
            if (frm, level) in moved:
                flv, fid = ("task" if level == "domain" else "domain"), frm
            if (to, level) in moved:
                tlv, tid = ("task" if level == "domain" else "domain"), to
            if flv is None or tlv is None or (frm, r["type"], to) in drop or (fid, r["type"], tid) in drop:
                continue
            have[(flv, fid, r["type"], tlv, tid)] = r
    for frm, typ, to, label, projs in dec.get("relations", []):
        flv, fid = _end(frm, ids)
        tlv, tid = _end(to, ids)
        for end, lv in ((frm, flv), (to, tlv)):
            if lv is None:
                errors.append(f"relation end {end} is not a shared concept")
        if flv is None or tlv is None:
            continue
        have[(flv, fid, typ, tlv, tid)] = {
            "type": typ, "label": label, "status": "hypothesis",
            "evidence": [{"path": f"projects/{p}", "kind": "graph"} for p in projs]}
    if errors:
        return errors
    out: dict[str, list] = {lv: [] for lv in shared}
    for (flv, fid, typ, tlv, tid), r in sorted(have.items()):
        out[flv].append({**r, "from": fid, "to": tid if flv == tlv else f"{tlv}:{tid}", "type": typ})
    for level, doc in shared.items():
        if out[level] or "relations" in doc:
            doc["relations"] = out[level]
    return []


def _rebind_shared(shared: dict, gone: set[str], moved: set[tuple[str, str]]) -> None:
    for doc in shared.values():
        for c in doc["concepts"]:
            maps = [m for m in c.get("maps_to", []) if m.get("target") not in gone]
            for m in maps:
                _follow_move(m, moved)
            if "maps_to" in c:
                c["maps_to"] = maps

