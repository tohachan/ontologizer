"""The checks on an update of the shared levels against their last release (docs/process.md §10).

Nothing here needs a new source: it holds the working levels to what was already released.

- every concept of the release is kept, changed, moved or removed; a removal needs a recorded reason
  (`removed:` in a consolidation file), and so does a relation that went away while its ends stayed
  (`remove_relations:`);
- a changed definition is checked against the evidence behind the concept, read at its cited commit;
- a competency question answered in the base run may not lose its answer, and the frame may not lose it;
- the report is current and the reasoner finds no new clash.

Readings that hold a shared concept apart (`shared.disputes`) do not block: they are listed, and `review apply`
marks the concept `disputed`.
A question the frame dropped on purpose is listed under `retired_questions: [{id, reason}]` in the frame, which
the owner approves; it is reported with its reason instead of blocking.
A hand-written competency-question status lowered on purpose carries `downgrade: {from, reason}` in the
run's `cq.yaml`; it is reported with its reason instead of blocking. A changed concept whose cited passages
support it only in part is a warning naming the passages, not a blocker (`_evidence_findings`).

The result is `runs/<run>/update-check.{json,md}` and a recorded gate. With `process.update_check: true`,
`release` of a level that has an earlier release requires that gate, with nothing blocking, over the
files as they are: any later change to them, a `review apply` included, means running it again.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from rdflib import Graph, Namespace

from ontologizer.core import frame as framing
from ontologizer.core.frame import Frame
from ontologizer.core.release_compare import release_concepts
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import LEVELS, application_docs, disputes, shared_concepts, shared_files
from ontologizer.core.sources import evidence_source
from ontologizer.core.verify import UNKNOWN_COMMIT, read_at
from ontologizer.ports.decisions import Question

OK = Namespace("urn:ontologizer:kit:")
RANK = {"answered": 2, "partial": 1}
COMPARED = ("label", "definition", "upper_category")
MIN_SHARE = 0.5   # `process.update_check_min_share`: below it, a changed concept's supporting share is a warning


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
        texts, unreadable = [], []
        for p, d in docs.items():
            root = Path(src.source_dir(p)) if src else None
            commit0 = (d.get("source") or {}).get("commit")
            for pc in d["concepts"]:
                kind = next((m.get("kind") for m in pc.get("maps_to", []) if m.get("target") == ref), False)
                if kind is False:
                    continue
                for e in pc.get("evidence", []):
                    if e.get("kind") in ("graph", "plan"):
                        continue
                    where = {"project": p, "concept": pc["id"], "mapping": kind, "path": e["path"], "lines": e.get("lines")}
                    t = read_at(root, e.get("commit") or commit0, e["path"], e.get("lines"))
                    if t and t is not UNKNOWN_COMMIT:
                        texts.append({**where, "text": t})
                    else:
                        unreadable.append(where)
        c = working[ref]
        passages = [{k: v for k, v in t.items() if k != "text"} for t in texts]
        row = {"evidence": len(texts), "judged": None, "supported": None, "passages": passages, "unreadable": unreadable}
        if texts and dec.on:
            state = {"claim": f"Concept '{c['label']}': {c['definition']}",
                     "evidence": [{"n": k, **{x: t[x] for x in ("path", "lines", "text", "project")}}
                                  for k, t in enumerate(texts)]}
            qs = [Question(f"e{k}", "noul", instructions=f"Do the lines of evidence n={k} support the claim? Answer "
                                                         f"by what the lines show, not by plausibility.")
                  for k in range(len(texts))]
            ans = dec.ask(state, qs)
            for k, ps in enumerate(passages):
                a = ans.get(f"e{k}")
                ps["verdict"] = "not judged" if a is None or not a.ok else \
                    ("supports" if a.value >= thr else "does not support")
            row["judged"] = sum(1 for ps in passages if ps["verdict"] != "not judged")
            row["supported"] = sum(1 for ps in passages if ps["verdict"] == "supports")
        out[ref] = row
    dec.done()
    return out


def _evidence_findings(ev: dict[str, dict], min_share: float) -> tuple[list[str], list[str]]:
    """What the evidence of the changed concepts says: (blocking, warnings).

    The passages behind a shared concept are those of every project concept mapped to it, under any mapping
    kind: a narrower (`specialization`) or `partial` counterpart's lines need not carry the whole shared
    definition, so a share below 1 is not a defect by itself and does not block. It is reported, with the
    passages that do not support the change, when it falls below `min_share`. A changed concept that no
    judged passage supports blocks: nothing behind it says what it now says.
    """
    blocking, warnings = [], []
    for ref, row in ev.items():
        n, judged, sup = row["evidence"], row.get("judged"), row["supported"]
        if row.get("unreadable"):
            warnings.append(f"{ref}: {len(row['unreadable'])} cited passage(s) could not be read at their commit: "
                            + _where(row["unreadable"]))
        if n == 0:
            warnings.append(f"{ref}: changed, with no cited passage to check it against")
            continue
        if sup is None:
            continue                                  # decisions off: not judged, said in the table
        if judged == 0:
            warnings.append(f"{ref}: none of its {n} cited passages could be judged (decision errors); run again")
            continue
        if sup == 0:
            of = f"its {n} cited passages" if judged == n else f"the {judged} judged of its {n} cited passages"
            blocking.append(f"{ref}: changed, and none of {of} supports it")
            continue
        if judged < n:
            warnings.append(f"{ref}: {n - judged} of {n} cited passages not judged (decision errors): "
                            + _where([p for p in row["passages"] if p.get("verdict") == "not judged"]))
        if sup / judged < min_share:
            warnings.append(f"{ref}: {sup} of {judged} judged passages support the changed definition "
                            f"(below {min_share:g}); not supporting: "
                            + _where([p for p in row["passages"] if p.get("verdict") == "does not support"]))
    return blocking, warnings


def _where(ps: list[dict]) -> str:
    return "; ".join(f"{p['path']}" + (f":{p['lines']}" if p.get("lines") else "")
                     + f" ({p['project']}:{p['concept']}, {p.get('mapping') or 'mapping kind not stated'})" for p in ps)


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
    min_share = float(run.process.get("update_check_min_share", MIN_SHARE))
    ev_block, warnings = _evidence_findings(ev, min_share)
    blocking += ev_block

    report = load_json(run.path("report.json"))
    base_report = load_json(run.cfg.root / (run.cfg.data.get("process") or {}).get("runs", "runs") / base_run / "report.json")
    cq_lost, cq_down, cq_retired = [], [], []
    retired = frame.retired_questions
    if report is None:
        blocking.append(f"no report for run {run.id}: run `ontologizer report`")
    else:
        rp = run.path("report.json")
        inputs = list(shared_files(run.cfg).values()) + [f for f in [run.path("cq.yaml")] if f.is_file()]
        stale = [f.name for f in inputs if f.stat().st_mtime > rp.stat().st_mtime]
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
                if str(retired.get(q["id"]) or "").strip():   # dropped by the owner's frame, with its reason
                    cq_retired.append({"id": q["id"], "was": q["status"], "reason": retired[q["id"]]})
                    continue
                cq_lost.append({"id": q["id"], "was": q["status"], "now": "no longer in the frame",
                                "hint": "if it was dropped on purpose, list it under `retired_questions: [{id, reason}]` "
                                        "in the frame and approve the frame again"})
            elif RANK.get(is_.get("status"), 0) < was:
                row = {"id": q["id"], "was": q["status"], "now": is_.get("status")}
                dg = is_.get("downgrade") or {}
                if dg.get("from") == q["status"] and str(dg.get("reason") or "").strip():
                    cq_down.append({**row, "reason": dg["reason"]})   # lowered on purpose, with the owner's reason
                    continue
                if dg:
                    row["hint"] = f"the downgrade in cq.yaml is from {dg.get('from')}, not {q['status']}"
                elif is_.get("how") not in ("query", "onto_cq_run"):
                    row["hint"] = "if the earlier hand-written status was overstated, record " \
                                  "`downgrade: {from, reason}` in cq.yaml"
                cq_lost.append(row)
        blocking += [f"question {x['id']}: was {x['was']}, now {x['now']}" + (f" ({x['hint']})" if x.get("hint") else "")
                     for x in cq_lost]
        a, b = (base_report.get("metrics") or {}).get("consistency_clashes"), (report.get("metrics") or {}).get("consistency_clashes")
        if b is not None and b > (a or 0):
            blocking.append(f"the reasoner finds {b} clash(es), {a or 0} in the base run")

    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    held = disputes(run.cfg, docs)
    from ontologizer.core.shared import stale_settlements
    warnings += [f"settled {x['concept']} -> {x['target']} ({x['file']}) settles nothing: {x['why']}"
                 for x in stale_settlements(run.cfg.root / "consolidation", docs)]
    open_disputes = {r: {"open": e["open"], "declared": e["declared"]} for r, e in sorted(held.items())
                     if r in working and (e["open"] or e["declared"])}
    covers = {str(f.relative_to(run.cfg.root)): framing.sha256(f) for f in shared_files(run.cfg).values()}
    doc = {"run": run.id, "at": now(), "base": base, "base_run": base_run, "mode": run.mode("verify"),
           "kept": len(kept), "changed": changed, "moved": moved, "added": added, "removed": removed,
           "relations_gone": rel_gone, "evidence": ev, "min_share": min_share, "questions_lost": cq_lost,
           "questions_downgraded": cq_down, "questions_retired": cq_retired, "disputes": open_disputes, "warnings": warnings, "blocking": blocking}
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
    if d.get("warnings"):
        lines += ["## Warnings", "", "Reported, not blocking: read the passages named and decide.", ""] \
            + [f"- {x}" for x in d["warnings"]] + [""]
    if d.get("questions_downgraded"):
        lines += ["## Questions downgraded with a reason", ""] \
            + [f"- {x['id']}: was {x['was']}, now {x['now']}: {x['reason']}" for x in d["questions_downgraded"]] + [""]
    if d.get("questions_retired"):
        lines += ["## Questions retired by the frame", ""] \
            + [f"- {x['id']}: was {x['was']}: {x['reason']}" for x in d["questions_retired"]] + [""]
    if d["changed"] or d["moved"]:
        lines += ["## Changed and moved", "",
                  "Cited passages: the lines of every project concept mapped to the concept, under any mapping kind. "
                  "Supporting: those the decision port judges to support the changed definition.", "",
                  "| Concept | Cited passages | Judged | Supporting | Share |", "| --- | --- | --- | --- | --- |"]
        for r, row in d["evidence"].items():
            sup, judged = row["supported"], row.get("judged", row["evidence"] if row["supported"] is not None else None)
            share = f"{sup / judged:.2f}" if sup is not None and judged else "—"
            lines.append(f"| `{r}` | {row['evidence']} | {'—' if judged is None else judged} | "
                         f"{'not judged' if sup is None else sup} | {share} |")
        lines.append("")
    if d["removed"]:
        lines += ["## Removed", ""] + [f"- `{x['ref']}` {x['label']}: {x['reason'] or 'no reason recorded'}"
                                       + (f" → {x['replaced_by']}" if x.get("replaced_by") else "") for x in d["removed"]] + [""]
    if d.get("disputes"):
        lines += ["## Disputed", "", "Not blocking. An open reading or a declared dispute makes the concept `disputed` "
                  "at `review apply`, until a consolidation file settles it with a reason.", ""]
        for r, e in d["disputes"].items():
            why = [x["concept"] for x in e["open"]] + ([e["declared"]] if e["declared"] else [])
            lines.append(f"- `{r}`: {'; '.join(why)}")
        lines.append("")
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
    changed = changed_since_check(cfg)
    if changed:
        return False, (f"{', '.join(changed)} changed after the update check (a `review apply` changes them too); "
                       f"run `ontologizer report`, then `ontologizer update-check {last['base']} --base-run "
                       f"{last['base_run']}` again")
    return True, f"update check against @{last['base']} is clean"


def changed_since_check(cfg) -> list[str]:
    """The files the last update-check gate covers that are missing or differ now; [] without such a gate.

    Any change counts, notes included: a `review apply` also sets statuses and removes rejected concepts and
    relations, which the check must see, so the gate is never carried over a change it did not read."""
    hits = [g for g in framing.gates(cfg) if g.get("gate") == "update-check"]
    if not hits:
        return []
    return [rel for rel, h in (hits[-1].get("covers") or {}).items()
            if not (cfg.root / rel).is_file() or framing.sha256(cfg.root / rel) != h]
