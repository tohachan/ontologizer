"""Apply a consolidation decision file to the shared-level and application JSONs.

Usage: ontologizer consolidate DECISIONS.yaml  (shared-level and application files from `paths:` of the config)

- adds the new shared domain/task concepts (evidence = supporting project concepts); a concept
  decided on another level than before moves there, and mappings to it follow;
- removes what `removed:` names ({ref, reason, replaced_by}), with its relations and the mappings to it;
  a constraint (shared or project) constraining only removed concepts goes too, a term naming one is unmarked,
  and a reference to a concept that moved level follows it;
- adds relations to those of earlier decisions, on the level of their `from` end. An end is a concept
  id, or `domain:<id>` / `task:<id>` when the id exists on both levels. `remove_relations:`
  ({relation: [from, type, to], reason}) takes relations out;
- rebinds project `candidate` mappings whose label was consolidated to the new IRI;
- adds mappings for supporting/attached project concepts that lacked one, as `support_kinds:` says
  ({ref: kind or {kind, overlap, differs}}; by default specialization, on the task level realization),
  and removes the ones this step added earlier for a concept no longer in the support;
- `settled:` ({concept, target, reason}) records why a partial or disputed project concept need not make
  its shared concept disputed, and `disputed:` ({ref, reason}) declares a dispute no mapping shows; a later
  `settled:` ({target, reason}) ends one. `review apply` acts on them (`shared.disputes`);
- `amend:` ({ref, reason, alt, add_support, drop_support, support_kinds}) changes those fields of a shared concept
  an earlier decision made, keeping its label, category, definition, status and note;
- `remap:` ({concept, target, kind, reason, overlap, differs}) re-kinds a project concept's mapping made at any
  earlier stage, or removes it with `kind: null`;
- `support.projects`/`families` are refreshed on every run from the evidence and the mappings onto each concept.
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

from ontologizer.core.constraints import prune as prune_constraints

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
    amends, errs = _amend(dec, shared, concepts, frame, Path(decision_file).name)
    errors += errs

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
    k_gone = k_changed = 0
    for level, doc in shared.items():
        if doc.get("constraints"):
            kept, g_, c_ = prune_constraints(doc["constraints"], lambda r, lv=level: _follow_ref(r, lv, gone, moved))
            doc["constraints"], k_gone, k_changed = kept, k_gone + len(g_), k_changed + len(c_)

    # rebind candidates
    rebound = added = unbound = 0
    for p, (f, d) in extractions.items():
        if d.get("constraints"):     # a project constraint naming a removed or moved shared concept follows it
            kept, g_, c_ = prune_constraints(d["constraints"], lambda r: _follow_ref(r, None, gone, moved))
            d["constraints"], k_gone, k_changed = kept, k_gone + len(g_), k_changed + len(c_)
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
                kind = att.get("kind") if ref in att.get("ids", []) else None
                added += _support_mapping(concepts[tuple(ref.split(":"))], tgt, level, kind,
                                          (n.get("support_kinds") or {}).get(ref))
    for tgt, add, drop, kinds in amends:   # an amended concept: only the support it names changes
        level = tgt.split(":")[0]
        for ref in drop:
            c = concepts[tuple(ref.split(":"))]
            maps = c.get("maps_to", [])
            kept = [m for m in maps if not (m.get("target") == tgt and str(m.get("note", "")).startswith(ADDED))]
            unbound += len(maps) - len(kept)
            c["maps_to"] = kept
        for ref in sorted(set(add) | set(kinds)):
            added += _support_mapping(concepts[tuple(ref.split(":"))], tgt, level, None, kinds.get(ref))
    remapped, errs = _remap(dec, shared, concepts, Path(decision_file).name)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    if frame is not None:
        _refresh_support(shared, concepts, frame)
    for lv in seeds:
        save(seeds[lv], shared[lv])
    if frame is not None:
        _warn_stale_settlements(Path(decision_file).parent, extractions)
    for p, (f, d) in extractions.items():
        save(f, d)
    left = sum(1 for (_, d) in extractions.values() for c in d["concepts"] for m in c.get("maps_to", []) if m.get("candidate"))
    print(f"domain concepts {len(domain_ids)}, task concepts {len(task_ids)}, relations {len(rels)}; "
          f"rebound {rebound} candidates, added {added} mappings, {left} single-project candidates left"
          + (f"; removed {len(gone)} concepts, {unbound} mappings" if gone or unbound else "")
          + (f"; constraints: {k_gone} removed with their concepts, {k_changed} with references changed"
             if k_gone or k_changed else "")
          + (f"; moved {len(moved)} concepts across levels" if moved else "")
          + (f"; amended {len(amends)} concepts" if amends else "")
          + (f"; changed {remapped} earlier mappings" if remapped else ""))
    return 0


def _warn_stale_settlements(decisions_dir: Path, extractions: dict) -> None:
    from ontologizer.core.shared import stale_settlements
    for x in stale_settlements(decisions_dir, {p: d for p, (_, d) in extractions.items()}):
        print(f"warning: settled {x['concept']} -> {x['target']} ({x['file']}) settles nothing: {x['why']}; "
              f"withdraw it with `unsettled: [{{concept, target, reason}}]`", file=sys.stderr)


def _support_mapping(c: dict, tgt: str, level: str, kind: str | None, how) -> int:
    """Give a supporting project concept its mapping to `tgt` when it has none; 1 when one was added.
    `support_kinds` may re-kind a mapping this step added, never one made at an earlier stage (`remap:` does that)."""
    how = {"kind": how} if isinstance(how, str) else (how or {})
    have = next((m for m in c.get("maps_to", []) if m.get("target") == tgt), None)
    if have is not None:
        if how and str(have.get("note", "")).startswith(ADDED):
            have.update(how)
        return 0
    kind = kind or ("realization" if level == "task" else "specialization")
    if level == "task" and kind == "roleOf":
        kind = "realization"
    c.setdefault("maps_to", []).append({"target": tgt, "kind": kind, "confidence": "medium",
                                        "note": f"{ADDED} {tgt}", **how})
    return 1


AMEND_KEYS = {"ref", "reason", "alt", "add_support", "drop_support", "support_kinds"}


def _graph_refs(c: dict) -> list[str]:
    """The supporting project concepts of a shared concept, as `project:id`, from its graph evidence."""
    out = []
    for e in c.get("evidence", []):
        path = str(e.get("path", ""))
        if e.get("kind") == "graph" and path.startswith("projects/") and "#" in path:
            p, _, cid = path[len("projects/"):].partition("#")
            out.append(f"{p}:{cid}")
    return out


def _amend(dec: dict, shared: dict, concepts: dict, frame, name: str) -> tuple[list, list[str]]:
    """`amend: [{ref, reason, alt?, add_support?, drop_support?, support_kinds?}]`: change some fields of a shared
    concept that an earlier decision made, keeping its label, category, definition, status and note. Changing one
    of those means naming the concept again in `new_domain`/`new_task`."""
    errs, out = [], []
    decided = {f"{lv}:{n['id']}" for lv, key in (("domain", "new_domain"), ("task", "new_task"))
               for n in dec.get(key, [])}
    for x in dec.get("amend") or []:
        ref = str(x.get("ref", ""))
        lv, _, cid = ref.partition(":")
        c = next((c for c in shared.get(lv, {}).get("concepts", []) if c["id"] == cid), None)
        extra = sorted(set(x) - AMEND_KEYS)
        if extra:
            errs.append(f"amend {ref}: {extra} cannot be amended; name the concept again to change its label, "
                        f"category or definition" if set(extra) & {"label", "def", "cat"} else f"amend {ref}: unknown {extra}")
        if not str(x.get("reason", "")).strip():
            errs.append(f"amend {ref}: `reason` required")
        if c is None:
            errs.append(f"amend {ref}: not a shared concept (`domain:<id>` or `task:<id>`)")
            continue
        if ref in decided:
            errs.append(f"amend {ref}: also decided as a concept in this file")
            continue
        add, drop = list(x.get("add_support") or []), list(x.get("drop_support") or [])
        kinds = dict(x.get("support_kinds") or {})
        if not (add or drop or kinds or "alt" in x):
            errs.append(f"amend {ref}: nothing to change")
        have = _graph_refs(c)
        for r in add:
            if tuple(r.split(":")) not in concepts:
                errs.append(f"amend {ref}: unknown project concept {r}")
        for r in drop:   # already out (this file applied before): nothing to do, said so
            if r not in have:
                if tuple(r.split(":")) not in concepts:
                    errs.append(f"amend {ref}: unknown project concept {r}")
                else:
                    print(f"amend {ref}: {r} is already out of its support", file=sys.stderr)
        drop = [r for r in drop if r in have]
        after = [r for r in have if r not in drop] + [r for r in add if r not in have]
        for r in kinds:
            if r not in after:
                errs.append(f"amend {ref}: support_kinds names {r}, which is not in its support")
        if not after:
            errs.append(f"amend {ref}: no support would be left; remove the concept instead")
        if errs:
            continue
        if "alt" in x:
            c["alt_labels"] = list(x["alt"] or [])
        c["evidence"] = ([e for e in c.get("evidence", []) if not (e.get("kind") == "graph" and any(
            e.get("path") == f"projects/{r.split(':')[0]}#{r.split(':')[1]}" for r in drop))]
            + [{"path": f"projects/{r.split(':')[0]}#{r.split(':')[1]}", "kind": "graph"} for r in add if r not in have])
        if frame is not None:
            s = c.setdefault("support", {})
            line = f"Amended in {name}: {x['reason']}"
            if line not in str(s.get("reason", "")):
                s["reason"] = f"{s.get('reason', '')} {line}".strip()
            s["amended"] = sorted(set(s.get("amended") or []) | {name})
        out.append((ref, add, drop, kinds))
    return out, errs


def _remap(dec: dict, shared: dict, concepts: dict, name: str) -> tuple[int, list[str]]:
    """`remap: [{concept, target, kind, reason, overlap?, differs?}]`: change the kind of a project concept's mapping
    made at any earlier stage, or remove it with `kind: null`. A removed mapping may not leave the concept in the
    target's support. Re-running changes nothing."""
    errs, n = [], 0
    support = {f"{lv}:{c['id']}": set(_graph_refs(c)) for lv, doc in shared.items() for c in doc["concepts"]}
    for x in dec.get("remap") or []:
        ref, tgt = str(x.get("concept", "")), str(x.get("target", ""))
        if not str(x.get("reason", "")).strip():
            errs.append(f"remap {ref} -> {tgt}: `reason` required")
        if "kind" not in x:
            errs.append(f"remap {ref} -> {tgt}: `kind` required (null removes the mapping)")
        c = concepts.get(tuple(ref.split(":")))
        if c is None:
            errs.append(f"remap {ref}: unknown project concept")
            continue
        m = next((m for m in c.get("maps_to", []) if m.get("target") == tgt), None)
        kind = x.get("kind")
        if m is None:
            if kind is not None:      # removed earlier and named again: nothing to change it to
                errs.append(f"remap {ref} -> {tgt}: no such mapping")
            continue
        if kind is None:
            if ref in support.get(tgt, set()):
                errs.append(f"remap {ref} -> {tgt}: it still supports {tgt}; drop it there first (amend: drop_support)")
                continue
        elif kind == "partial" and not ((x.get("overlap") or m.get("overlap")) and (x.get("differs") or m.get("differs"))):
            errs.append(f"remap {ref} -> {tgt}: a partial mapping needs `overlap` and `differs`")
            continue
        if errs:
            continue
        if kind is None:
            c["maps_to"] = [k for k in c["maps_to"] if k is not m]
            n += 1
            continue
        if m.get("kind") != kind:
            old = str(m.get("note", "")).strip()
            if old and not old.startswith("Earlier, as "):   # the earlier note explains the earlier kind, so it says so
                old = f"Earlier, as {m.get('kind')}: {old}"
            m["note"] = f"{old} Changed in step 3 ({name}) from {m.get('kind')} to {kind}: {x['reason']}".strip()
            m["kind"] = kind
            n += 1
        for k in ("overlap", "differs"):
            if x.get(k):
                m[k] = x[k]
        if kind != "partial":
            m.pop("overlap", None)
            m.pop("differs", None)
    return n, errs


def _refresh_support(shared: dict, concepts: dict, frame) -> None:
    """`support.projects` and `families` say who stands behind a shared concept now: the projects of its evidence
    and of every project concept mapped onto it, as stage 7 counts them. Refreshed on every run."""
    mapped: dict[str, set[str]] = {}
    for (p, _), c in concepts.items():
        for m in c.get("maps_to", []):
            if m.get("target"):
                mapped.setdefault(m["target"], set()).add(p)
    for lv, doc in shared.items():
        for c in doc["concepts"]:
            if "support" not in c:
                continue
            projs = {r.split(":")[0] for r in _graph_refs(c)} | mapped.get(f"{lv}:{c['id']}", set())
            c["support"]["projects"] = sorted(projs)
            c["support"]["families"] = sorted({f for f in (frame.family_of(p) for p in projs) if f})


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
    for x in dec.get("unsettled") or []:
        if not (x.get("concept") and x.get("target") and str(x.get("reason", "")).strip()):
            errs.append(f"unsettled {x.get('concept')}: `concept`, `target` and `reason` required")
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


def _follow_ref(ref: str, level: str | None, gone: set[str], moved: set[tuple[str, str]]) -> str | None:
    """A constraint's reference after this step: None when its concept is removed, the other level when it
    moved. `level`: the shared level whose file holds the constraint (its local ids are of that level), or None
    for a project file (only prefixed references name shared concepts)."""
    if ":" not in ref and level is None:
        return ref
    lv, cid = ref.partition(":")[::2] if ":" in ref else (level, ref)
    if lv == "local" and level is not None:
        lv = level
    if f"{lv}:{cid}" in gone:
        return None
    if (cid, lv) in moved:
        lv = "task" if lv == "domain" else "domain"
    return cid if lv == level else f"{lv}:{cid}"


def _rebind_shared(shared: dict, gone: set[str], moved: set[tuple[str, str]]) -> None:
    for doc in shared.values():
        for c in doc["concepts"]:
            maps = [m for m in c.get("maps_to", []) if m.get("target") not in gone]
            for m in maps:
                _follow_move(m, moved)
            if "maps_to" in c:
                c["maps_to"] = maps

