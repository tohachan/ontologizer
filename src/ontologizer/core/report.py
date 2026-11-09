"""Stage 7: leave-one-out stability and the run report (docs/process.md §7, §Metrics).

Support of a shared concept = the corpus projects with a concept mapped to it
(application files), counted by architectural family. Leave-one-out removes
each project in turn: `stable` keeps ≥ min families under every removal,
`fragile` names the projects whose removal breaks it, `unsupported` lacks
support even with every project.

The report collects every metric of the process document from the run
directory, the answer status of each competency question, the decision modes
and costs, and who extracted what. Two reports compare mechanically.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from ontologizer.core.frame import Frame, FrameError
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import application_docs, shared_concepts
from ontologizer.core.verify import final_items

DEFAULT_MIN_FAMILIES = 2
CQ_STATUSES = ("answered", "partial", "unanswered")


def min_families(run: Run) -> int:
    from ontologizer.core.levels import Preset
    return Preset.load(run.cfg.preset_path).min_families


def support(run: Run, frame: Frame) -> dict[str, dict]:
    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    out = {s["ref"]: {"label": s["label"], "level": s["level"], "projects": set()} for s in shared_concepts(run.cfg)}
    for p, d in docs.items():
        for c in d["concepts"]:
            for m in c.get("maps_to", []):
                if m.get("target") in out:
                    out[m["target"]]["projects"].add(p)
    return out


def stability(run: Run, frame: Frame) -> dict:
    need = min_families(run)
    admission = frame.admission_families(need)
    sup = support(run, frame)
    rows = {}
    for ref, s in sorted(sup.items()):
        fams = {frame.family_of(p) for p in s["projects"]}
        breaks = []
        for p in sorted(s["projects"]):
            if len({frame.family_of(q) for q in s["projects"] - {p}}) < need:
                breaks.append(p)
        if len(fams) < need:   # admitted below the confirmation threshold is not the same as without support
            mark = "unconfirmed" if len(fams) >= admission else "unsupported"
        else:
            mark = "fragile" if breaks else "stable"
        rows[ref] = {"label": s["label"], "level": s["level"], "projects": sorted(s["projects"]),
                     "families": sorted(fams), "mark": mark, "breaks_without": breaks}
    marks = Counter(r["mark"] for r in rows.values())
    doc = {"run": run.id, "at": now(), "min_families": need, "admission_families": admission, "concepts": rows, "marks": dict(marks),
           "stable_share": round(marks["stable"] / len(rows), 3) if rows else None}
    save_json(run.path("stability.json"), doc)
    return doc


def _check_cq(manual: dict, frame: Frame, path) -> None:
    """A malformed answer file must not read as questions nobody answered. A plain value is metadata."""
    ids = {q["id"] for q in frame.questions}
    errs = []
    for k, v in manual.items():
        if not isinstance(v, dict):
            continue
        if k not in ids:
            errs.append(f"{k}: not a question of the frame ({sorted(ids)}); ids are top-level keys")
        elif v.get("status") not in CQ_STATUSES:
            errs.append(f"{k}.status: one of {CQ_STATUSES}")
    if errs:
        raise FrameError([f"{path}: {e}" for e in errs])


def _cq_status(run: Run, frame: Frame, engine=None) -> list[dict]:
    manual = {}
    p = run.path("cq.yaml")
    if p.is_file():
        manual = yaml.safe_load(p.read_text()) or {}
        _check_cq(manual, frame, p)
    engine_cq = _engine_cq(run, frame, engine)
    out = []
    for q in frame.questions:
        st = {"id": q["id"], "level": q["level"], "question": q["question"]}
        if q.get("query") and q["id"] in engine_cq:
            st.update(engine_cq[q["id"]])
        elif q.get("query") and engine is not None:
            qp = run.cfg.root / q["query"]
            try:
                rows = engine.select(qp.read_text())
                st.update(status="answered" if rows else "unanswered", how="query", rows=len(rows))
            except Exception as e:   # the store may be down: fall back to the manual status
                st.update(status=None, how=f"query failed: {type(e).__name__}")
        if not st.get("status"):
            m = manual.get(q["id"]) or {}
            st.update(status=m.get("status", "unassessed"), how=st.get("how", "manual" if m else "none"),
                      by=m.get("by", []), note=m.get("note"))
        if q.get("target"):
            st["target"] = q["target"]
            st["projects"] = (manual.get(q["id"]) or {}).get("projects") or {}
        out.append(st)
    return out


def _engine_cq(run: Run, frame: Frame, engine) -> dict[str, dict]:
    """The engine's competency-question runner (`onto_cq_run`) for questions with a query:
    rows, pass/fail and the engine's query pitfalls. Empty when the engine has no tool call."""
    qs = [q for q in frame.questions if q.get("query")]
    if not qs or engine is None or not hasattr(engine, "tool"):
        return {}
    payload = [{"id": q["id"], "question": q["question"], "sparql": (run.cfg.root / q["query"]).read_text(),
                "expected_min_rows": 1} for q in qs]
    try:
        r = engine.tool("onto_cq_run", cqs_json=json.dumps(payload))
    except Exception:   # older engine or store down: the kit's own SELECT check runs instead
        return {}
    out = {}
    for x in (r.get("results") if isinstance(r, dict) else None) or []:
        out[x["id"]] = {"status": "answered" if x.get("passed") else "unanswered", "how": "onto_cq_run",
                        "rows": x.get("row_count"), "pitfalls": x.get("pitfalls", [])}
    return out


def consistency(engine) -> dict | None:
    """RDFS reasoning over the store as a dry run (`onto_reason`, nothing materialised): the
    clashes the engine finds, by rule. Releases have version-distinct IRIs, so a clash is a
    contradiction inside one version, not one version against another."""
    if engine is None or not hasattr(engine, "tool"):
        return None
    try:
        r = engine.tool("onto_reason", profile="rdfs", materialize=False)
    except Exception as e:
        return {"error": type(e).__name__}
    inc = (r or {}).get("inconsistency") or {}
    subjects = sorted({p[0] for c in inc.get("clashes", []) for p in c.get("premises", [])
                       if str(p[0]).startswith("<urn:")})
    return {"clashes": inc.get("clash_count", 0), "by_rule": inc.get("by_rule", {}), "subjects": subjects[:50],
            "inferred": r.get("inferred_count"), "scope": (r.get("scope") or {}).get("selector")}


FIT_SCORE = {"full": 1.0, "partial": 0.5, "none": 0.0}


def target_fit(frame: Frame, cqs: list[dict], projects: list[str]) -> dict:
    """Per design target: each requirement (a tagged competency question) × project → full|partial|none,
    and each project's fit = mean score over the assessed requirements."""
    out = {}
    for t in frame.targets:
        reqs = [q for q in cqs if q.get("target") == t["id"]]
        fit = {}
        for p in projects:
            vals = [q["projects"].get(p) for q in reqs if q["projects"].get(p) in FIT_SCORE]
            fit[p] = {"assessed": len(vals), "of": len(reqs),
                      "fit": round(sum(FIT_SCORE[v] for v in vals) / len(vals), 3) if vals else None}
        out[t["id"]] = {"label": t["label"], "requirements": [
            {"id": q["id"], "question": q["question"], "by": q.get("by", []), "projects": q["projects"]} for q in reqs],
            "fit": fit, "ranking": sorted((p for p in projects if fit[p]["fit"] is not None),
                                          key=lambda p: (-fit[p]["fit"], p))}
    return out


def _decision_stats(run: Run) -> dict:
    stats: dict[str, dict] = {}
    for e in run.log_entries(with_state=False):
        s = stats.setdefault(e["stage"], {"modes": Counter(), "decisions": 0, "cost": 0.0, "requests": set()})
        s["modes"][e["mode"]] += 1
        s["decisions"] += 1
        r = e.get("receipt") or {}
        if r.get("cost_share") is not None:
            s["cost"] += float(r["cost_share"])
        elif r.get("cost") is not None:
            s["cost"] += float(r["cost"]) / max(1, int(r.get("batch") or 1))
        if r:
            s["requests"].add(r.get("request_id") or (e["at"], r.get("batch")))
    return {k: {"modes": dict(v["modes"]), "decisions": v["decisions"], "requests": len(v["requests"]),
                "cost_usd": round(v["cost"], 4)} for k, v in sorted(stats.items())}


def report(run: Run, frame: Frame, engine=None) -> dict:
    projects = [p for p in frame.projects if p not in frame.overlay]
    per = {}
    harnesses: Counter = Counter()
    extractions = []
    agree, cat_cmp, cat_dis, tgt_cmp, tgt_dis = [], 0, 0, 0, 0
    agree_all: list[float] = []
    policies: dict[str, int] = {}
    recheck_types: dict[str, dict] = {}
    ev_checked = ev_sup = ev_final = 0
    ev_kinds: dict[str, list[int]] = {}
    cov_num = cov_den = 0
    unmeasured = []
    for p in projects:
        row: dict = {}
        inv = load_json(run.path("inventory", f"{p}.json"))
        app = load_json(run.cfg.path("application") / f"{p}.json")
        if inv is None or inv.get("status") != "measured":
            row["coverage"] = "unmeasured"
            unmeasured.append(p)
        elif app is not None:
            counted = {c["id"] for c in inv["candidates"] if c.get("counted")}
            cited = {x for c in app["concepts"] for x in c.get("inventory", [])} & counted
            row["coverage"] = round(len(cited) / len(counted), 3) if counted else None
            row["inventory"] = {"counted": len(counted), "covered": len(cited), "total": inv.get("total")}
            cov_num += len(cited)
            cov_den += len(counted)
        rec = load_json(run.path("reconcile", f"{p}.json"))
        if rec:
            row["agreement"] = rec["agreement"]
            row["agreement_mean"] = rec["agreement_mean"]
            if rec.get("agreement_all") is not None:
                row["agreement_all"] = rec["agreement_all"]
                agree_all.append(rec["agreement_all"])
            agree.append(rec["agreement_mean"])
            for e, meta in rec.get("extractor_meta", {}).items():
                harnesses[meta.get("harness", "claude")] += 1
                extractions.append({"project": p, "extractor": e, **meta})
        m = load_json(run.path("map", f"{p}.json"))
        if m:
            cd = m["category_disagreement"]
            if m["modes"]["category"] == "jev":
                cat_cmp += cd["compared"]
                cat_dis += cd["disagree"]
            row["category_disagreement"] = cd
            jt = (m.get("applied") or {}).get("jev_llm_target")
            if jt:
                tgt_cmp += jt["compared"]
                tgt_dis += jt["disagree"]
                row["target_disagreement"] = jt
        v = load_json(run.path("verify", f"{p}.json"))
        if v:
            row["verification"] = v["evidence"]
            row["absences"] = len(v.get("absences", []))
            row["recheck"] = (v.get("applied") or {}).get("recheck")
            if v["evidence"]["share"] is not None:
                ev_checked += v["evidence"]["checked"]
                ev_sup += v["evidence"]["supported"]
                done = sum(1 for i in final_items(run, p, v) if i["final"] == "supported")
                ev_final += done
                by_kind = ev_kinds.setdefault(frame.source_kind(p), [0, 0])
                by_kind[0] += done
                by_kind[1] += v["evidence"]["checked"]
            for t, x in ((v.get("applied") or {}).get("recheck_by_type") or {}).items():
                acc = recheck_types.setdefault(t, {"rechecked": 0, "changed": 0})
                acc["rechecked"] += x["rechecked"]
                acc["changed"] += x["changed"]
            pol = ((v.get("applied") or {}).get("recheck") or {}).get("policy")
            if pol:
                policies[pol] = policies.get(pol, 0) + 1
            rh = ((v.get("applied") or {}).get("recheck") or {}).get("harness")
            if rh:
                harnesses[f"recheck:{rh}"] += 1
        if app:
            row["concepts"] = len(app["concepts"])
            row["mapped"] = sum(1 for c in app["concepts"] if c.get("maps_to"))
        per[p] = row
    st = stability(run, frame)   # recomputed: mappings may have changed since the last `stability`
    cqs = _cq_status(run, frame, engine)
    answered = sum(1 for q in cqs if q["status"] == "answered")
    cross = any(h != "claude" and not h.startswith("recheck:claude") for h in harnesses)
    shared = {ref: {"label": r["label"], "level": r["level"], "mark": r["mark"], "projects": r["projects"],
                    "families": r["families"]} for ref, r in st["concepts"].items()}
    metrics = {
        "inventory_coverage": round(cov_num / cov_den, 3) if cov_den else "unmeasured",
        "inventory_unmeasured": unmeasured,
        "extraction_agreement": round(sum(agree) / len(agree), 3) if agree else None,
        "extraction_agreement_all": round(sum(agree_all) / len(agree_all), 3) if agree_all else None,
        "jev_llm_disagreement": {
            "category": {"compared": cat_cmp, "disagree": cat_dis,
                         "rate": round(cat_dis / cat_cmp, 3) if cat_cmp else None},
            "target": {"compared": tgt_cmp, "disagree": tgt_dis,
                       "rate": round(tgt_dis / tgt_cmp, 3) if tgt_cmp else None}},
        "verified_evidence_share": round(ev_sup / ev_checked, 3) if ev_checked else None,
        "verified_evidence_share_after_recheck": round(ev_final / ev_checked, 3) if ev_checked else None,
        "verified_evidence_by_source_kind": {k: round(a / b, 3) for k, (a, b) in sorted(ev_kinds.items()) if b} or None,
        "recheck_change_rate": {t: round(x["changed"] / x["rechecked"], 3) for t, x in sorted(recheck_types.items())
                                if x["rechecked"]} or None,
        "loo_stability": st["stable_share"],
        "cq_coverage": round(answered / len(cqs), 3) if cqs else None,
    }
    cons = consistency(engine)
    metrics["consistency_clashes"] = cons.get("clashes") if cons and "clashes" in cons else None
    doc = {"run": run.id, "at": now(), "study": frame.h.get("study"), "projects": projects,
           "descriptions": [p for p in projects if frame.source_kind(p) == "description"],
           "metrics": metrics, "per_project": per, "stability_marks": st["marks"], "shared": shared,
           "competency_questions": cqs, "targets": target_fit(frame, cqs, projects),
           "consistency": cons,
           "decisions": _decision_stats(run),
           "modes": {s: run.mode(s) for s in ("inventory", "reconcile", "map", "category", "group", "verify")},
           "extractions": extractions, "harnesses": dict(harnesses), "recheck_policy": policies or None,
           "cross_harness": cross if extractions else None,
           "harness_note": None if cross else "Claude models only: no cross-harness results in this run"}
    save_json(run.path("report.json"), doc)
    run.path("report.md").write_text(render(doc))
    return doc


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def render(doc: dict) -> str:
    m = doc["metrics"]
    j = m["jev_llm_disagreement"]
    lines = [f"# Run report: {doc['run']}", "", f"Study `{doc['study']}`, {len(doc['projects'])} projects, "
             f"generated {doc['at']}.", "", "## Metrics", "", "| Metric | Value |", "| --- | --- |",
             f"| Inventory coverage | {_fmt(m['inventory_coverage'])} |",
             f"| Extraction agreement (mean) | {_fmt(m['extraction_agreement'])} |",
             f"| Recheck change rate by item type (stage 6) | "
             f"{', '.join(f'{k} {v:.3f}' for k, v in (m.get('recheck_change_rate') or {}).items()) or '—'} |",
             f"| Extraction agreement, all extractors (clusters every extractor found) | "
             f"{_fmt(m.get('extraction_agreement_all'))} |",
             f"| Jev–LLM disagreement, category | {_fmt(j['category']['rate'])} ({j['category']['disagree']}/{j['category']['compared']}) |",
             f"| Jev–LLM disagreement, mapping target | {_fmt(j['target']['rate'])} ({j['target']['disagree']}/{j['target']['compared']}) |",
             f"| Verified-evidence share, first judgement | {_fmt(m['verified_evidence_share'])} |",
             f"| Verified-evidence share, after the recheck | {_fmt(m.get('verified_evidence_share_after_recheck'))} |",
             *([f"| Verified evidence, after the recheck, by kind of source | "
                f"{', '.join(f'{k} {v:.3f}' for k, v in m['verified_evidence_by_source_kind'].items())} |"]
               if len(m.get("verified_evidence_by_source_kind") or {}) > 1 else []),
             f"| Leave-one-out stability | {_fmt(m['loo_stability'])} |",
             f"| Competency-question coverage | {_fmt(m['cq_coverage'])} |",
             f"| Consistency clashes (engine reasoning) | {_fmt(m.get('consistency_clashes'))} |", ""]
    if doc.get("descriptions"):
        lines += [f"Descriptions, not implementations: {', '.join(doc['descriptions'])}. Their verified evidence means "
                  "the text says so, not that it holds in a running system.", ""]
    if (doc.get("stability_marks") or {}).get("unconfirmed"):
        lines += [f"{doc['stability_marks']['unconfirmed']} shared concepts are unconfirmed: admitted without the "
                  "independent support that confirmation needs, so they stay hypotheses.", ""]
    if m["inventory_unmeasured"]:
        lines += [f"Coverage unmeasured for: {', '.join(m['inventory_unmeasured'])}.", ""]
    c = doc.get("consistency")
    if c is not None:
        lines += [f"Consistency (engine reasoning, RDFS, dry run over the store): "
                  + (f"{c['clashes']} clash(es) {c['by_rule']}" if "clashes" in c else f"not run ({c.get('error')})")
                  + ".", ""]
    if doc.get("harness_note"):
        lines += [f"**{doc['harness_note']}.**", ""]
    if doc.get("recheck_policy"):
        lines += ["Rechecks by policy (process.recheck): "
                  + ", ".join(f"{k} {v}" for k, v in sorted(doc["recheck_policy"].items())) + ".", ""]
    lines += ["## Per project", "", "| Project | Concepts | Mapped | Coverage | Agreement | Verified | Absences |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for p in doc["projects"]:
        r = doc["per_project"].get(p, {})
        ver = (r.get("verification") or {}).get("share")
        lines.append(f"| {p} | {_fmt(r.get('concepts'))} | {_fmt(r.get('mapped'))} | {_fmt(r.get('coverage'))} | "
                     f"{_fmt(r.get('agreement_mean'))} | {_fmt(ver)} | {_fmt(r.get('absences'))} |")
    lines += ["", "## Shared concepts", "", "| Concept | Level | Mark | Families | Projects |", "| --- | --- | --- | --- | --- |"]
    for ref, s in sorted(doc["shared"].items()):
        lines.append(f"| {s['label']} (`{ref}`) | {s['level']} | {s['mark']} | {', '.join(s['families'])} | "
                     f"{', '.join(s['projects'])} |")
    lines += ["", "## Competency questions", "", "| Id | Level | Status | Question |", "| --- | --- | --- | --- |"]
    for q in doc["competency_questions"]:
        lines.append(f"| {q['id']} | {q['level']} | {q['status']} | {q['question']} |")
    for tid, t in (doc.get("targets") or {}).items():
        ps = doc["projects"]
        lines += ["", f"## Target: {t['label']}", "", "| Requirement | " + " | ".join(ps) + " | Parts to reuse |",
                  "| --- |" + " --- |" * (len(ps) + 1)]
        for r in t["requirements"]:
            cells = [{"full": "●", "partial": "◐", "none": "○"}.get(r["projects"].get(p), "—") for p in ps]
            lines.append(f"| {r['id']}: {r['question']} | " + " | ".join(cells) + f" | {', '.join(r['by']) or '—'} |")
        lines.append("| **fit** | " + " | ".join(_fmt(t["fit"][p]["fit"]) for p in ps) + " | |")
        if t["ranking"]:
            lines += ["", "Ranking: " + ", ".join(f"{p} ({_fmt(t['fit'][p]['fit'])})" for p in t["ranking"]) + "."]
    lines += ["", "## Decisions", "", "| Stage | Mode (config) | Decisions | Requests | Cost, USD |", "| --- | --- | --- | --- | --- |"]
    for s, mode in doc["modes"].items():
        d = doc["decisions"].get(s, {})
        lines.append(f"| {s} | {mode} | {d.get('decisions', 0)} | {d.get('requests', 0)} | {d.get('cost_usd', 0)} |")
    lines += ["", "## Extractions", "", "| Project | Extractor | Harness | Model | Run id |", "| --- | --- | --- | --- | --- |"]
    for e in doc["extractions"]:
        lines.append(f"| {e['project']} | {e['extractor']} | {e.get('harness', '—')} | {e.get('model', '—')} | "
                     f"{e.get('run_id', '—')} |")
    return "\n".join(lines) + "\n"


def compare(a: dict, b: dict) -> dict:
    """Mechanical comparison of two run reports: metric deltas and the shared concepts kept, added, dropped."""
    def flat(m: dict, pre: str = "") -> dict:
        out = {}
        for k, v in m.items():
            if isinstance(v, dict):
                out.update(flat(v, f"{pre}{k}."))
            elif not isinstance(v, list):
                out[f"{pre}{k}"] = v
        return out
    fa, fb = flat(a["metrics"]), flat(b["metrics"])
    metrics = {}
    for k in sorted(set(fa) | set(fb)):
        x, y = fa.get(k), fb.get(k)
        d = round(y - x, 3) if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) else None
        metrics[k] = {"a": x, "b": y, "delta": d}
    sa, sb = set(a["shared"]), set(b["shared"])
    la = {(v["label"].lower()): k for k, v in a["shared"].items()}
    renamed = {}
    for k in sb - sa:
        lab = b["shared"][k]["label"].lower()
        if lab in la and la[lab] in sa - sb:
            renamed[la[lab]] = k
    return {"a": a["run"], "b": b["run"], "metrics": metrics,
            "shared": {"kept": sorted(sa & sb), "added": sorted(sb - sa - set(renamed.values())),
                       "dropped": sorted(sa - sb - set(renamed)), "renamed": renamed},
            "marks": {k: {"a": a["shared"][k]["mark"], "b": b["shared"][k]["mark"]}
                      for k in sorted(sa & sb) if a["shared"][k]["mark"] != b["shared"][k]["mark"]}}


def compare_pairs(dir_a: Path, dir_b: Path) -> dict:
    """Stage 3 in two runs over the same extractions: the matched pairs of each extractor pair,
    in both runs, only in A, only in B (an ablation shows what the decisions changed)."""
    from ontologizer.core.reconcile import match
    out = {}
    for fa in sorted((Path(dir_a) / "reconcile").glob("*.json")):
        if fa.name.endswith(".resolutions.json"):
            continue
        fb = Path(dir_b) / "reconcile" / fa.name
        if not fb.is_file():
            continue
        ra, rb = json.loads(fa.read_text()), json.loads(fb.read_text())
        for k in sorted(set(ra.get("pairs", {})) & set(rb.get("pairs", {}))):
            ma = {(x, y) for x, y, _ in match(ra["pairs"][k]["judged"], ra["threshold"])}
            mb = {(x, y) for x, y, _ in match(rb["pairs"][k]["judged"], rb["threshold"])}
            out[f"{ra['project']} {k}"] = {"both": len(ma & mb), "only_a": sorted(ma - mb), "only_b": sorted(mb - ma),
                                           "a_mode": ra.get("mode"), "b_mode": rb.get("mode")}
    return out


def render_pairs(pairs: dict, a: str, b: str) -> str:
    lines = ["", "## Stage 3 matched pairs", "", f"| Project, extractors | Both | Only {a} | Only {b} |",
             "| --- | --- | --- | --- |"]
    tot = [0, 0, 0]
    for k, v in pairs.items():
        lines.append(f"| {k} | {v['both']} | {len(v['only_a'])} | {len(v['only_b'])} |")
        tot = [tot[0] + v["both"], tot[1] + len(v["only_a"]), tot[2] + len(v["only_b"])]
    lines.append(f"| **all** | {tot[0]} | {tot[1]} | {tot[2]} |")
    return "\n".join(lines) + "\n"


def render_compare(c: dict) -> str:
    lines = [f"# Run comparison: {c['a']} → {c['b']}", "", "| Metric | A | B | Δ |", "| --- | --- | --- | --- |"]
    for k, v in c["metrics"].items():
        lines.append(f"| {k} | {_fmt(v['a'])} | {_fmt(v['b'])} | {_fmt(v['delta'])} |")
    s = c["shared"]
    lines += ["", f"Shared concepts: {len(s['kept'])} kept, {len(s['added'])} added, {len(s['dropped'])} dropped, "
              f"{len(s['renamed'])} renamed.", ""]
    for k in ("added", "dropped"):
        if s[k]:
            lines.append(f"- {k}: {', '.join(s[k])}")
    for x, y in s["renamed"].items():
        lines.append(f"- renamed: {x} → {y}")
    for k, v in c["marks"].items():
        lines.append(f"- mark changed: {k} {v['a']} → {v['b']}")
    return "\n".join(lines) + "\n"


def load_report(path: str | Path) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "report.json"
    return json.loads(p.read_text())
