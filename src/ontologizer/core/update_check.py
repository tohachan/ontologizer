"""The checks on an update of the shared levels against their last release (docs/process.md §10).

Nothing here needs a new source: it holds the working levels to what was already released.

- every concept of the release is kept, changed, moved or removed; a removal needs a recorded reason
  (`removed:` in a consolidation file), and so does a relation that went away while its ends stayed
  (`remove_relations:`);
- a changed definition is checked against the evidence behind the concept, read at its cited commit;
- a competency question answered in the base run may not lose its answer, and the frame may not lose it;
- the report is current and the reasoner finds no new clash.

The result is `runs/<run>/update-check.{json,md}` and a recorded gate. With `process.update_check: true`,
`release` of a level that has an earlier release requires that gate, with nothing blocking, over the
files as they are.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from rdflib import Graph, Namespace

from ontologizer.core import frame as framing
from ontologizer.core.frame import Frame
from ontologizer.core.release_compare import release_concepts
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import LEVELS, application_docs, shared_concepts, shared_files
from ontologizer.core.sources import evidence_source
from ontologizer.core.verify import UNKNOWN_COMMIT, read_at
from ontologizer.ports.decisions import Question

OK = Namespace("urn:ontologizer:kit:")
RANK = {"answered": 2, "partial": 1}
COMPARED = ("label", "definition", "upper_category")


class UpdateCheckError(ValueError):
    pass


def _decisions(run: Run) -> tuple[dict[str, dict], set[tuple[str, str, str]]]:
    """What the consolidation files of this owner record as removed, with the reasons."""
    removed, rel = {}, set()
    for p in sorted((run.cfg.root / "consolidation").glob("*.yaml")):
        d = yaml.safe_load(p.read_text()) or {}
        for x in d.get("removed") or []:
            removed[x["ref"]] = x
        for x in d.get("remove_relations") or []:
            rel.add(tuple(x["relation"]))
    return removed, rel


def _release_relations(run: Run, engine, version: str) -> dict[tuple[str, str, str], dict]:
    cfg, out = run.cfg, {}
    ns = {lv: cfg.term_ns(f"{cfg.shared[lv].split('@')[0]}@{version}") for lv in LEVELS if cfg.shared.get(lv)}

    def ref(term) -> str | None:
        return next((f"{lv}:{str(term)[len(n):]}" for lv, n in ns.items() if str(term).startswith(n)), None)

    for lv in ns:
        text = engine.export_graph(cfg.graph_iri(f"{cfg.shared[lv].split('@')[0]}@{version}"))
        g = Graph()
        if text.strip():
            g.parse(data=text, format="turtle")
        for r in g.subjects(OK.relFrom, None):
            a, b, t = ref(g.value(r, OK.relFrom)), ref(g.value(r, OK.relTo)), g.value(r, OK.relType)
            if a and b and t is not None:
                out[(a, str(t).rsplit(":", 1)[-1], b)] = {"from": a, "to": b, "type": str(t).rsplit(":", 1)[-1]}
    return out


def _working_relations(run: Run) -> set[tuple[str, str, str]]:
    from ontologizer.core.review import _ref
    import json
    out = set()
    for lv, f in shared_files(run.cfg).items():
        for r in json.loads(f.read_text()).get("relations", []):
            out.add((_ref(lv, r["from"]), r["type"], _ref(lv, r["to"])))
    return out


def _evidence_check(run: Run, frame: Frame, changed: list[str], working: dict[str, dict]) -> dict[str, dict]:
    """Do the lines behind a changed concept support its new definition? Through the decision port."""
    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    src = evidence_source(run.cfg)
    dec = run.decider("verify")
    thr = run.threshold("support")
    out = {}
    for ref in changed:
        texts = []
        for p, d in docs.items():
            root = Path(src.source_dir(p)) if src else None
            commit0 = (d.get("source") or {}).get("commit")
            for pc in d["concepts"]:
                if not any(m.get("target") == ref for m in pc.get("maps_to", [])):
                    continue
                for e in pc.get("evidence", []):
                    if e.get("kind") in ("graph", "plan"):
                        continue
                    t = read_at(root, e.get("commit") or commit0, e["path"], e.get("lines"))
                    if t and t is not UNKNOWN_COMMIT:
                        texts.append({"path": e["path"], "lines": e.get("lines"), "text": t, "project": p})
        c = working[ref]
        row = {"evidence": len(texts), "supported": None}
        if texts and dec.on:
            state = {"claim": f"Concept '{c['label']}': {c['definition']}",
                     "evidence": [{"n": k, **t} for k, t in enumerate(texts)]}
            qs = [Question(f"e{k}", "noul", instructions=f"Do the lines of evidence n={k} support the claim? Answer "
                                                         f"by what the lines show, not by plausibility.")
                  for k in range(len(texts))]
            ans = dec.ask(state, qs)
            row["supported"] = sum(1 for k in range(len(texts)) if (a := ans.get(f"e{k}")) is not None and a.ok
                                   and a.value >= thr)
        out[ref] = row
    dec.done()
    return out


def check(run: Run, frame: Frame, engine, base: str, base_run: str) -> dict:
    if base_run == run.id:
        raise UpdateCheckError(f"--base-run {base_run} is this run: an update is a new run, so the two can be compared")
    old = release_concepts(run.cfg, engine, base)
    if not old:
        raise UpdateCheckError(f"no concepts in the shared graphs at @{base}: is it released?")
    working = {s["ref"]: s for s in shared_concepts(run.cfg)}
    removed_rec, rel_rec = _decisions(run)
    blocking, kept, changed, moved, removed = [], [], [], [], []
    for ref, c in sorted(old.items()):
        lv, _, cid = ref.partition(":")
        other = f"{'task' if lv == 'domain' else 'domain'}:{cid}"
        if ref in working:
            w = working[ref]
            (changed if any((w.get(k) or "") != (c.get(k) or "") for k in COMPARED) else kept).append(ref)
        elif other in working:
            moved.append({"from": ref, "to": other})
        else:
            rec = removed_rec.get(ref)
            removed.append({"ref": ref, "label": c["label"], "reason": (rec or {}).get("reason"),
                            "replaced_by": (rec or {}).get("replaced_by")})
            if not rec:
                blocking.append(f"{ref}: removed with no reason recorded (`removed:` in a consolidation file)")
    added = sorted(r for r in working if r not in old and not any(m["to"] == r for m in moved))

    moved_map = {m["from"]: m["to"] for m in moved}
    now_rel = _working_relations(run)
    rel_gone = []
    for (a, t, b) in sorted(_release_relations(run, engine, base)):
        a2, b2 = moved_map.get(a, a), moved_map.get(b, b)
        if (a2, t, b2) in now_rel or a2 not in working or b2 not in working:
            continue     # kept, or an end was removed, which is recorded above
        names = {(x.split(":", 1)[-1]) for x in (a2, b2)}
        recorded = any(r[1] == t and {x.split(":", 1)[-1] for x in (r[0], r[2])} == names for r in rel_rec)
        rel_gone.append({"from": a, "type": t, "to": b, "recorded": recorded})
        if not recorded:
            blocking.append(f"{a} {t} {b}: relation gone with no reason recorded (`remove_relations:`)")

    to_check = changed + [m["to"] for m in moved]
    ev = _evidence_check(run, frame, to_check, working)
    for ref, row in ev.items():
        if row["supported"] == 0:
            blocking.append(f"{ref}: changed, and none of its {row['evidence']} cited passages supports it")

    report = load_json(run.path("report.json"))
    base_report = load_json(run.cfg.root / (run.cfg.data.get("process") or {}).get("runs", "runs") / base_run / "report.json")
    cq_lost = []
    if report is None:
        blocking.append(f"no report for run {run.id}: run `ontologizer report`")
    else:
        rp = run.path("report.json")
        stale = [f.name for f in shared_files(run.cfg).values() if f.stat().st_mtime > rp.stat().st_mtime]
        if stale:
            blocking.append(f"the report is older than {stale}: run `ontologizer report` again")
    if base_report is None:
        blocking.append(f"no report for base run {base_run}")
    if report is not None and base_report is not None:
        current = {q["id"]: q for q in report.get("competency_questions", [])}
        for q in base_report.get("competency_questions", []):
            was = RANK.get(q.get("status"), 0)
            if not was:
                continue
            is_ = current.get(q["id"])
            if is_ is None:
                cq_lost.append({"id": q["id"], "was": q["status"], "now": "no longer in the frame"})
            elif RANK.get(is_.get("status"), 0) < was:
                cq_lost.append({"id": q["id"], "was": q["status"], "now": is_.get("status")})
        blocking += [f"question {x['id']}: was {x['was']}, now {x['now']}" for x in cq_lost]
        a, b = (base_report.get("metrics") or {}).get("consistency_clashes"), (report.get("metrics") or {}).get("consistency_clashes")
        if b is not None and b > (a or 0):
            blocking.append(f"the reasoner finds {b} clash(es), {a or 0} in the base run")

    covers = {str(f.relative_to(run.cfg.root)): framing.sha256(f) for f in shared_files(run.cfg).values()}
    doc = {"run": run.id, "at": now(), "base": base, "base_run": base_run, "mode": run.mode("verify"),
           "kept": len(kept), "changed": changed, "moved": moved, "added": added, "removed": removed,
           "relations_gone": rel_gone, "evidence": ev, "questions_lost": cq_lost, "blocking": blocking}
    save_json(run.path("update-check.json"), doc)
    run.path("update-check.md").write_text(render(doc))
    framing.record(run.cfg, {"gate": "update-check", "file": str(run.path("update-check.json").relative_to(run.cfg.root)),
                             "base": base, "base_run": base_run, "blocking": len(blocking), "at": doc["at"],
                             "covers": covers})
    return doc


def render(d: dict) -> str:
    lines = [f"# Update check: {d['run']} against @{d['base']}", "",
             f"{d['kept']} kept, {len(d['changed'])} changed, {len(d['moved'])} moved between levels, "
             f"{len(d['added'])} added, {len(d['removed'])} removed.", ""]
    if d["blocking"]:
        lines += ["## Blocking", ""] + [f"- {x}" for x in d["blocking"]] + [""]
    else:
        lines += ["Nothing blocks the release.", ""]
    if d["changed"] or d["moved"]:
        lines += ["## Changed and moved", "", "| Concept | Cited passages | Supporting |", "| --- | --- | --- |"]
        for r, row in d["evidence"].items():
            lines.append(f"| `{r}` | {row['evidence']} | {'not judged' if row['supported'] is None else row['supported']} |")
        lines.append("")
    if d["removed"]:
        lines += ["## Removed", ""] + [f"- `{x['ref']}` {x['label']}: {x['reason'] or 'no reason recorded'}"
                                       + (f" → {x['replaced_by']}" if x.get("replaced_by") else "") for x in d["removed"]] + [""]
    if d["added"]:
        lines += ["## Added", ""] + [f"- `{x}`" for x in d["added"]] + [""]
    return "\n".join(lines) + "\n"


def require_update_check(cfg, engine) -> tuple[bool, str]:
    """`release`: with `process.update_check`, a level with an earlier release needs a clean check of these files."""
    if not (cfg.data.get("process") or {}).get("update_check"):
        return True, "not required"
    bases = [cfg.shared[lv].split("@")[0] for lv in LEVELS if cfg.shared.get(lv)]
    names = [cfg.graph_path(n) for n in engine.graph_names()]
    if not any(n.startswith(f"{b}@") for n in names for b in bases):
        return True, "first release"
    hits = [g for g in framing.gates(cfg) if g.get("gate") == "update-check"]
    if not hits:
        return False, "this level has an earlier release and no update check (`ontologizer update-check BASE --base-run RUN`)"
    last = hits[-1]
    if last.get("blocking"):
        return False, f"the update check against @{last['base']} has {last['blocking']} blocking item(s): {last['file']}"
    for rel, h in (last.get("covers") or {}).items():
        f = cfg.root / rel
        if not f.is_file() or framing.sha256(f) != h:
            return False, f"{rel} changed after the update check; run it again"
    return True, f"update check against @{last['base']} is clean"
