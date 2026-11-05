"""Compare two releases of the shared levels by meaning (docs/process.md §7, *Release comparison*).

Two releases built by independent runs name the same concept differently, so ids alone do not
say what was kept. Code proposes candidate pairs across the releases (TF-IDF over label, alt
labels and definition, top K each way, plus equal labels); the decision port judges them in
batches (`noul`: the same concept?, stage `compare`); with decisions off, a pair is the same when
a label is equal. Accepted pairs form the correspondence:

- one A concept ↔ one B concept: `kept` (same id and level) or `renamed`;
- one A ↔ several B: `split`; several A ↔ one B: `merged`; several ↔ several: `regrouped`;
- an A concept without a counterpart: `dropped`; a B concept without one: `added`.

Reasons come from the run that built B: an added concept carries its consolidation reason and
its review verdict. A dropped concept is traced through that run's project concepts: its
counterparts among them (judged like the pairs above) and what became of them, as one of

- `absorbed`: they map to B concepts, which cover the A concept as a kind, part or overlap;
- `rejected`: their group was eligible and consolidation or review rejected it, with the reason;
- `one-family`: their group had support from fewer families than the preset requires;
- `project-only`: they stayed project concepts in no group;
- `no-run-counterpart`: no project concept of the run is judged the same (the nearest is named).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ontologizer.core.config import Config
from ontologizer.core.levels import Preset
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import LEVELS, application_docs, graph_concepts
from ontologizer.core.similarity import TfIdf, same_label
from ontologizer.ports.decisions import Question

TOP_K = 3
MIN_SIM = 0.12
PAIR_BATCH = 20   # pairs per decision state: one adapter request each

TASK = ("Concepts of two independently built versions of one shared ontology of a software domain. "
        "Decide for each pair whether both denote the same concept, even under different names.")
TRACE_TASK = ("A concept of an earlier version of a shared ontology, and concepts that software projects "
              "name in their own terms. Decide for each pair whether the project concept denotes the "
              "same concept as the earlier one, or a kind of it.")


def _brief(c: dict) -> dict:
    return {"label": c["label"], "alt_labels": c.get("alt_labels", []), "definition": c.get("definition", ""),
            "category": c.get("upper_category")}


def release_concepts(cfg: Config, engine, version: str) -> dict[str, dict]:
    """`{<level>:<id>: concept}` of the shared domain and task graphs at `version`."""
    out = {}
    for lv in LEVELS:
        g = cfg.shared.get(lv)
        if not g:
            continue
        base = g.split("@")[0]
        for c in graph_concepts(cfg, engine, f"{base}@{version}"):
            out[f"{lv}:{c['id']}"] = {**c, "level": lv}
    return out


def candidate_pairs(a: dict[str, dict], b: dict[str, dict], top_k: int = TOP_K) -> list[tuple[str, str, float]]:
    """Candidate (a, b, sim) pairs: the top K of each side by TF-IDF, plus every equal label."""
    tf = TfIdf({**{f"A|{k}": v for k, v in a.items()}, **{f"B|{k}": v for k, v in b.items()}})
    pairs: dict[tuple[str, str], float] = {}
    for x in a:
        scored = sorted(((tf.sim(f"A|{x}", f"B|{y}"), y) for y in b), reverse=True)
        for s, y in scored[:top_k]:
            if s >= MIN_SIM:
                pairs[(x, y)] = s
        for y in b:
            if same_label(a[x], b[y]):
                pairs[(x, y)] = max(pairs.get((x, y), 0.0), tf.sim(f"A|{x}", f"B|{y}"))
    for y in b:
        scored = sorted(((tf.sim(f"A|{x}", f"B|{y}"), x) for x in a), reverse=True)
        for s, x in scored[:top_k]:
            if s >= MIN_SIM:
                pairs[(x, y)] = s
    return sorted(((x, y, s) for (x, y), s in pairs.items()), key=lambda t: (-t[2], t[0], t[1]))


def judge(run: Run, stage: str, task: str, pairs: list[tuple[str, str, float]], a: dict, b: dict) -> list[dict]:
    """`{a, b, sim, p}` per pair: P(same) from the decision port in batches, or label equality when off."""
    dec = run.decider(stage)
    out = []
    if dec.on:
        for i in range(0, len(pairs), PAIR_BATCH):
            chunk = pairs[i:i + PAIR_BATCH]
            state = {"task": task, "pairs": [{"n": n, "first": _brief(a[x]), "second": _brief(b[y])}
                                             for n, (x, y, _) in enumerate(chunk)]}
            qs = [Question(f"p{n}", "noul", instructions=f"Pair n={n}: are 'first' and 'second' the same "
                                                         f"concept (not merely related)?")
                  for n in range(len(chunk))]
            ans = dec.ask(state, qs)
            for n, (x, y, s) in enumerate(chunk):
                v = ans.get(f"p{n}")
                if v is not None and v.ok:
                    out.append({"a": x, "b": y, "sim": round(s, 3), "p": round(float(v.value), 3)})
    else:
        out = [{"a": x, "b": y, "sim": round(s, 3), "p": 1.0 if same_label(a[x], b[y]) else 0.0}
               for x, y, s in pairs]
    dec.done()
    return out


def correspondence(a: dict, b: dict, accepted: list[tuple[str, str]]) -> list[dict]:
    """Components of the accepted bipartite pairs, classified; unmatched concepts alone."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for x, y in accepted:
        parent[find(f"B|{y}")] = find(f"A|{x}")
    comps: dict[str, list[str]] = {}
    for k in [f"A|{x}" for x in a] + [f"B|{y}" for y in b]:
        comps.setdefault(find(k), []).append(k)
    out = []
    for mem in comps.values():
        xa = sorted(m[2:] for m in mem if m.startswith("A|"))
        yb = sorted(m[2:] for m in mem if m.startswith("B|"))
        if not yb:
            kind = "dropped"
        elif not xa:
            kind = "added"
        elif len(xa) == 1 and len(yb) == 1:
            kind = "kept" if xa[0] == yb[0] else "renamed"
        elif len(xa) == 1:
            kind = "split"
        elif len(yb) == 1:
            kind = "merged"
        else:
            kind = "regrouped"
        out.append({"kind": kind, "a": xa, "b": yb})
    order = {"kept": 0, "renamed": 1, "split": 2, "merged": 3, "regrouped": 4, "dropped": 5, "added": 6}
    return sorted(out, key=lambda c: (order[c["kind"]], (c["a"] or c["b"])[0]))


# ---- reasons from the run that built B -------------------------------------------------
def _consolidation(run: Run) -> dict:
    p = run.cfg.root / "consolidation" / f"{run.id}.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}


def _review(run: Run) -> dict[str, dict]:
    p = run.path("review.yaml")
    d = (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}
    return {i["ref"]: i for i in d.get("items", [])}


def added_reasons(run: Run, refs: list[str]) -> dict[str, dict]:
    cons = _consolidation(run)
    entries = {f"{lv}:{e['id']}": e for lv in LEVELS for e in cons.get(f"new_{lv}") or []}
    rv = _review(run)
    out = {}
    for r in refs:
        e, v = entries.get(r) or {}, rv.get(r) or {}
        out[r] = {"reason": e.get("reason"), "support": e.get("support"), "verdict": v.get("verdict"),
                  "review_note": v.get("comment") or v.get("note")}
    return out


def trace_dropped(run: Run, a: dict[str, dict], dropped: list[str], b_refs: set[str], frame=None) -> dict[str, dict]:
    """What became of each dropped A concept in the run that built B (module docstring); the
    judged pairs are under the key `""`."""
    if not dropped:
        return {"": []}
    overlay = set(frame.overlay) if frame is not None else set()
    docs = {p: d for p, d in application_docs(run.cfg).items() if p not in overlay}
    pc = {f"{p}:{c['id']}": {**c, "project": p} for p, d in docs.items() for c in d.get("concepts", [])}
    sub = {k: a[k] for k in dropped}
    pairs = []
    if pc:
        tf = TfIdf({**{f"A|{k}": v for k, v in sub.items()}, **{f"P|{k}": v for k, v in pc.items()}})
        for x in dropped:
            scored = sorted(((tf.sim(f"A|{x}", f"P|{y}"), y) for y in pc), reverse=True)[:TOP_K * 2]
            pairs += [(x, y, s) for s, y in scored if s >= MIN_SIM or same_label(sub[x], pc[y])]
    judged = judge(run, "compare", TRACE_TASK, pairs, sub, pc)
    thr = run.threshold("compare_same_concept")
    groups = load_json(run.path("consolidate", "groups.json"), {}) or {}
    member_of = {m: g for g in groups.get("groups", []) for m in g["members"]}
    cons = _consolidation(run)
    rejected = {r["group"]: r.get("reason") for r in cons.get("rejected") or [] if r.get("group")}
    review_rej = {r: (i.get("comment") or i.get("note")) for r, i in _review(run).items() if i.get("verdict") == "reject"}
    need = Preset.load(run.cfg.preset_path).min_families
    out: dict = {"": judged}
    for x in dropped:
        mine = sorted((j for j in judged if j["a"] == x), key=lambda j: -j["p"])
        hits = [j for j in mine if j["p"] >= thr]
        cps = [j["b"] for j in hits]
        if not cps:
            near = mine[0] if mine else None
            out[x] = {"reason": "no-run-counterpart", "detail": "no project concept of the run corresponds to it"
                      + (f" (nearest `{near['b']}` {pc[near['b']]['label']}, P {near['p']:.2f})" if near else ""),
                      "counterparts": []}
            continue
        maps = sorted({(m["target"], m["kind"]) for k in cps for m in pc[k].get("maps_to", [])
                       if m.get("target") in b_refs})
        gs = sorted({member_of[k]["id"] for k in cps if k in member_of})
        rej = {g: rejected[g] for g in gs if g in rejected}
        revj = {m[0]: review_rej[m[0]] for m in maps if m[0] in review_rej}
        if maps:
            kind, detail = "absorbed", "; ".join(f"{t} ({k})" for t, k in maps)
        elif rej or revj:
            kind, detail = "rejected", "; ".join(f"{g}: {r}" for g, r in {**rej, **revj}.items())
        elif gs:
            fams = sorted({f for g in groups["groups"] if g["id"] in gs for f in g["families"]})
            kind, detail = "one-family", f"groups {', '.join(gs)}: families {fams} (< {need})"
        else:
            projects = sorted({pc[k]["project"] for k in cps})
            kind, detail = "project-only", f"project concepts in {projects}, in no group of two families"
        out[x] = {"reason": kind, "detail": detail,
                  "counterparts": [{"concept": j["b"], "label": pc[j["b"]]["label"], "p": j["p"]} for j in hits]}
    return out


# ---- metrics ---------------------------------------------------------------------------
def release_metrics(cs: dict[str, dict]) -> dict:
    n = len(cs)
    by_level = {lv: sum(1 for c in cs.values() if c["level"] == lv) for lv in LEVELS}
    status: dict[str, int] = {}
    for c in cs.values():
        status[c.get("status") or "?"] = status.get(c.get("status") or "?", 0) + 1
    ev = [c.get("evidence_count", 0) for c in cs.values()]
    return {"concepts": n, **{f"concepts_{lv}": v for lv, v in by_level.items()},
            "asserted_share": round(status.get("asserted", 0) / n, 3) if n else None,
            "status": dict(sorted(status.items())),
            "evidence_per_concept": round(sum(ev) / n, 2) if n else None,
            "with_two_evidence_share": round(sum(1 for e in ev if e >= 2) / n, 3) if n else None}


def project_metrics(cfg: Config, engine, version: str, frame, shared_refs: set[str]) -> dict:
    """From the project graphs released with the shared levels (`<project graph>@<version>`): project
    concepts, the share mapped onto the release, family support and leave-one-out stability of the
    shared concepts, as stage 7 computes them for a run. A release without project graphs: {}."""
    from rdflib import Graph, Namespace
    if frame is None:
        return {}
    ok = Namespace("urn:ontologizer:kit:")
    need = Preset.load(cfg.preset_path).min_families
    ns_of = {}
    for lv in LEVELS:
        if cfg.shared.get(lv):
            ns_of[cfg.term_ns(f"{cfg.shared[lv].split('@')[0]}@{version}")] = lv
    docs = application_docs(cfg)
    have = set(engine.graph_names())
    sup: dict[str, set[str]] = {r: set() for r in shared_refs}
    n_concepts = n_mapped = n_projects = 0
    for p in frame.projects:
        if p in frame.overlay or p not in docs:
            continue
        gpath = f"{docs[p]['graph']}@{version}"
        if cfg.graph_iri(gpath) not in have:
            continue
        g = Graph()
        g.parse(data=engine.export_graph(cfg.graph_iri(gpath)), format="turtle")
        pns = cfg.term_ns(gpath)
        concepts = {s for s in g.subjects(None, ok.Concept) if str(s).startswith(pns)}
        mapped = set()
        for m in g.subjects(None, ok.Mapping):
            t, src = g.value(m, ok.mappingTarget), g.value(m, ok.mappingSource)
            for ns, lv in ns_of.items():
                if t is not None and str(t).startswith(ns):
                    ref = f"{lv}:{str(t)[len(ns):]}"
                    if ref in sup:
                        sup[ref].add(p)
                        mapped.add(src)
        n_projects += 1
        n_concepts += len(concepts)
        n_mapped += len(mapped & concepts)
    if not n_projects:
        return {}
    marks = {"stable": 0, "fragile": 0, "unsupported": 0}
    for ref, ps in sup.items():
        fams = {frame.family_of(x) for x in ps}
        if len(fams) < need:
            marks["unsupported"] += 1
        elif any(len({frame.family_of(q) for q in ps - {x}}) < need for x in ps):
            marks["fragile"] += 1
        else:
            marks["stable"] += 1
    n = len(sup)
    return {"projects": n_projects, "project_concepts": n_concepts,
            "mapped_share": round(n_mapped / n_concepts, 3) if n_concepts else None,
            "supported_share": round((n - marks["unsupported"]) / n, 3) if n else None,
            "loo_stability": round(marks["stable"] / n, 3) if n else None, "marks": marks}


def _run_metrics(run: Run | None) -> dict | None:
    if run is None:
        return None
    rep = load_json(run.path("report.json"))
    return None if rep is None else {"run": rep["run"], "metrics": rep["metrics"], "decisions": rep.get("decisions")}


def compare(cfg: Config, engine, a: str, b: str, run: Run, run_a: Run | None = None, frame=None) -> dict:
    ca, cb = release_concepts(cfg, engine, a), release_concepts(cfg, engine, b)
    if not ca or not cb:
        raise ValueError(f"no concepts in the shared graphs at @{a if not ca else b}: is it released?")
    pairs = candidate_pairs(ca, cb)
    judged = judge(run, "compare", TASK, pairs, ca, cb)
    thr = run.threshold("compare_same_concept")
    accepted = [(j["a"], j["b"]) for j in judged if j["p"] >= thr]
    corr = correspondence(ca, cb, accepted)
    p_of = {(j["a"], j["b"]): j["p"] for j in judged}
    for c in corr:
        c["labels_a"] = [ca[x]["label"] for x in c["a"]]
        c["labels_b"] = [cb[y]["label"] for y in c["b"]]
        c["p"] = [p_of[(x, y)] for x in c["a"] for y in c["b"] if (x, y) in p_of and p_of[(x, y)] >= thr]
        if c["kind"] in ("kept", "renamed") and ca[c["a"][0]]["level"] != cb[c["b"][0]]["level"]:
            c["level_changed"] = True
    added = [y for c in corr if c["kind"] == "added" for y in c["b"]]
    dropped = [x for c in corr if c["kind"] == "dropped" for x in c["a"]]
    reasons_add = added_reasons(run, added)
    reasons_drop = trace_dropped(run, ca, dropped, set(cb), frame)
    trace = reasons_drop.pop("")
    for c in corr:
        if c["kind"] == "added":
            c["why"] = reasons_add[c["b"][0]]
        elif c["kind"] == "dropped":
            c["why"] = reasons_drop[c["a"][0]]
        if c["kind"] in ("added", "dropped"):   # the best candidate across releases, below the threshold
            side, other = ("b", "a") if c["kind"] == "added" else ("a", "b")
            mine = sorted((j for j in judged if j[side] == c[side][0]), key=lambda j: -j["p"])
            if mine:
                o = mine[0][other]
                c["nearest"] = {"ref": o, "label": (ca if other == "a" else cb)[o]["label"], "p": mine[0]["p"]}
    counts: dict[str, int] = {}
    for c in corr:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    dec = [e for e in run.log_entries(with_state=False) if e["stage"] == "compare"]
    cost = sum(float((e.get("receipt") or {}).get("cost_share") or 0) for e in dec)
    return {"a": a, "b": b, "run": run.id, "at": now(), "mode": run.mode("compare"), "threshold": thr,
            "pairs_judged": len(judged), "pairs_accepted": len(accepted), "counts": counts,
            "a_kept": sum(1 for c in corr if c["kind"] not in ("dropped", "added") for _ in c["a"]),
            "b_kept": sum(1 for c in corr if c["kind"] not in ("dropped", "added") for _ in c["b"]),
            "metrics": {"a": {**release_metrics(ca), **project_metrics(cfg, engine, a, frame, set(ca))},
                        "b": {**release_metrics(cb), **project_metrics(cfg, engine, b, frame, set(cb))}},
            "run_metrics": {"a": _run_metrics(run_a), "b": _run_metrics(run)},
            "decisions": {"logged": len(dec), "cost_usd": round(cost, 4)},
            "correspondence": corr, "judged": judged, "trace_judged": trace}


def _f(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _near(x: dict) -> str:
    n = x.get("nearest")
    return f"{n['label']} (`{n['ref']}`, P {n['p']:.2f})" if n else "—"


def render(c: dict) -> str:
    a, b = c["a"], c["b"]
    lines = [f"# Release comparison: @{a} → @{b}", "",
             f"Run `{c['run']}`, {c['at']}. Pairs judged {c['pairs_judged']} (mode `{c['mode']}`, threshold "
             f"{c['threshold']}), accepted {c['pairs_accepted']}; decision cost ${c['decisions']['cost_usd']}.", "",
             "| Correspondence | Count |", "| --- | --- |"]
    for k in ("kept", "renamed", "split", "merged", "regrouped", "dropped", "added"):
        lines.append(f"| {k} | {c['counts'].get(k, 0)} |")
    ma, mb = c["metrics"]["a"], c["metrics"]["b"]
    lines += ["", f"@{a}: {c['a_kept']} of {ma['concepts']} concepts have a counterpart in @{b}; "
                  f"@{b}: {c['b_kept']} of {mb['concepts']} have one in @{a}.", "",
              "## Metrics", "", f"| Metric | @{a} | @{b} | Δ |", "| --- | --- | --- | --- |"]
    for k in ma:
        if k in ("status", "marks"):
            continue
        x, y = ma.get(k), mb.get(k)
        d = round(y - x, 3) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None
        lines.append(f"| {k} | {_f(x)} | {_f(y)} | {_f(d)} |")
    lines.append(f"| status | {ma['status']} | {mb['status']} | |")
    if "marks" in ma or "marks" in mb:
        lines.append(f"| stability marks | {ma.get('marks', '—')} | {mb.get('marks', '—')} | |")
        lines += ["", "Project metrics are computed from the project graphs released with each version, as stage 7 "
                      "computes them for a run (family support of each shared concept, leave-one-out)."]
    ra, rb = c["run_metrics"]["a"], c["run_metrics"]["b"]
    if rb:
        lines += ["", f"Run metrics (`report.json`): @{a} {'run ' + ra['run'] if ra else 'has no run: unmeasured'}, "
                      f"@{b} run `{rb['run']}`.", "", f"| Run metric | @{a} | @{b} |", "| --- | --- | --- |"]
        for k, v in rb["metrics"].items():
            if isinstance(v, (dict, list)):
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        x = ((ra or {}).get("metrics", {}).get(k) or {}).get(kk, {}) if ra else None
                        lines.append(f"| {k}.{kk} | {_f(x.get('rate') if isinstance(x, dict) else x) if ra else 'unmeasured'}"
                                     f" | {_f(vv.get('rate') if isinstance(vv, dict) else vv)} |")
                continue
            x = (ra or {}).get("metrics", {}).get(k) if ra else "unmeasured"
            lines.append(f"| {k} | {_f(x)} | {_f(v)} |")
    lines += ["", "## Correspondence", "", f"| Kind | @{a} | @{b} | P(same) |", "| --- | --- | --- | --- |"]
    for x in c["correspondence"]:
        if x["kind"] in ("dropped", "added"):
            continue
        lev = " (level changed)" if x.get("level_changed") else ""
        lines.append(f"| {x['kind']}{lev} | {', '.join(f'{l} (`{r}`)' for r, l in zip(x['a'], x['labels_a']))} | "
                     f"{', '.join(f'{l} (`{r}`)' for r, l in zip(x['b'], x['labels_b']))} | "
                     f"{', '.join(f'{p:.2f}' for p in x['p'])} |")
    lines += ["", f"## Dropped: in @{a}, no counterpart in @{b}", "",
              f"| Concept | Why | Detail | Run counterparts | Nearest in @{b} (below threshold) |",
              "| --- | --- | --- | --- | --- |"]
    for x in c["correspondence"]:
        if x["kind"] == "dropped":
            w = x["why"]
            cps = ", ".join(f"`{k['concept']}` ({k['p']:.2f})" for k in w["counterparts"][:4]) or "—"
            lines.append(f"| {x['labels_a'][0]} (`{x['a'][0]}`) | {w['reason']} | {w['detail']} | {cps} | "
                         f"{_near(x)} |")
    lines += ["", f"## Added: in @{b}, no counterpart in @{a}", "",
              f"| Concept | Review | Consolidation reason | Nearest in @{a} (below threshold) |", "| --- | --- | --- | --- |"]
    for x in c["correspondence"]:
        if x["kind"] == "added":
            w = x["why"]
            lines.append(f"| {x['labels_b'][0]} (`{x['b'][0]}`) | {w.get('verdict') or '—'} | "
                         f"{(w.get('reason') or '—').replace('|', '/')} | {_near(x)} |")
    return "\n".join(lines) + "\n"


def write(run: Run, c: dict) -> Path:
    out = run.path(f"release-compare-{c['a']}-{c['b']}.json")
    save_json(out, c)
    out.with_suffix(".md").write_text(render(c))
    return out.with_suffix(".md")


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def translate(c: dict) -> dict[str, list[str]]:
    """A ref → the B refs it corresponds to (empty when dropped), from a comparison."""
    out: dict[str, list[str]] = {}
    for x in c["correspondence"]:
        for r in x["a"]:
            out[r] = list(x["b"])
    return out
