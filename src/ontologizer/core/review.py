"""Stage 8: owner review, gold set and calibration (docs/process.md §8).

- `review build` lists every domain and task concept (no cap) with its
  support, families, stability, the verification share of its supporting
  concepts and their evidence, for the owner to mark accept / reject / revise.
- `review apply` makes accepted concepts `asserted`, removes rejected ones
  (and the project mappings to them), notes revisions, and records the
  `review` gate; `release` of a shared level requires that gate.
- `gold sample` draws a stratified sample of logged decisions (evidence
  claims, categories, same-concept pairs) for the owner to label, without
  showing the model's answer.
- `gold calibrate` compares the logged answers with the labels, chooses the
  thresholds that maximise accuracy and writes them with the measured
  accuracy to `calibration.yaml`.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

from ontologizer.core import frame as framing
from ontologizer.core.frame import Frame
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import application_docs, shared_files

VERDICTS = ("accept", "reject", "revise")
GRID = [round(0.05 * i, 2) for i in range(1, 20)]
# decision stage → (gold kind, threshold it calibrates)
STAGE_KIND = {"verify": ("evidence", "support"), "reconcile": ("same-concept", "same_concept"),
              "group": ("group-same-concept", "group_same_concept"), "category": ("category", None),
              "inventory": ("triage", "triage_keep"), "compare": ("compare-same-concept", "compare_same_concept")}


class ReviewError(ValueError):
    pass


def review_path(run: Run) -> Path:
    return run.path("review.yaml")


def build(run: Run, frame: Frame) -> dict:
    st = load_json(run.path("stability.json"), {}) or {}
    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    from ontologizer.core.verify import final_items
    ver = {p: final_items(run, p) for p in docs}
    items = []
    for lv, f in shared_files(run.cfg).items():
        for c in json.loads(f.read_text()).get("concepts", []):
            ref = f"{lv}:{c['id']}"
            sup = []
            for p, d in docs.items():
                for pc in d["concepts"]:
                    if any(m.get("target") == ref for m in pc.get("maps_to", [])):
                        vi = [i for i in ver[p] if i["claim"] == f"concept:{pc['id']}"
                              and i["final"] in ("supported", "unsupported", "missing")]
                        ok = sum(1 for i in vi if i["final"] == "supported")
                        sup.append({"concept": f"{p}:{pc['id']}", "label": pc["label"],
                                    "kind": next(m["kind"] for m in pc["maps_to"] if m.get("target") == ref),
                                    "verified": f"{ok}/{len(vi)}" if vi else "unchecked",
                                    "evidence": [f"{e['path']}:{e.get('lines', '')}" for e in pc.get("evidence", [])[:2]]})
            s = (st.get("concepts") or {}).get(ref, {})
            items.append({"ref": ref, "label": c["label"], "category": c["upper_category"],
                          "definition": c["definition"], "status": c.get("status", "hypothesis"),
                          "families": s.get("families", []), "stability": s.get("mark", "unknown"),
                          "breaks_without": s.get("breaks_without", []), "reason": (c.get("support") or {}).get("reason"),
                          "support": sup, "verdict": None, "comment": None})
    doc = {"run": run.id, "built": now(),
           "instructions": "Set `verdict` of every item to accept, reject or revise (with `comment`), then run "
                           "`ontologizer review apply --by <name>`.", "items": items}
    p = review_path(run)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():   # keep verdicts already given
        old = {i["ref"]: i for i in (yaml.safe_load(p.read_text()) or {}).get("items", [])}
        for i in items:
            if old.get(i["ref"], {}).get("verdict"):
                i["verdict"], i["comment"] = old[i["ref"]]["verdict"], old[i["ref"]].get("comment")
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
    return {"items": len(items), "path": str(p)}


def apply(run: Run, by: str, effort: str | None = None) -> dict:
    p = review_path(run)
    if not p.is_file():
        raise ReviewError("no review.yaml: run `ontologizer review build` first")
    doc = yaml.safe_load(p.read_text()) or {}
    items = doc.get("items", [])
    missing = [i["ref"] for i in items if i.get("verdict") not in VERDICTS]
    if missing:
        raise ReviewError(f"{len(missing)} item(s) without a verdict (accept|reject|revise): {missing[:10]}")
    bad = [i["ref"] for i in items if i["verdict"] == "revise" and not i.get("comment")]
    if bad:
        raise ReviewError(f"revise needs a comment: {bad[:10]}")
    verdict = {i["ref"]: i for i in items}
    counts = {v: 0 for v in VERDICTS}
    from ontologizer.core.levels import Preset
    confirmation = Preset.load(run.cfg.preset_path).min_families
    lowered = Frame.load(run.cfg).admission_families(confirmation) < confirmation
    unconfirmed = 0
    rejected = set()
    for lv, f in shared_files(run.cfg).items():
        d = json.loads(f.read_text())
        keep = []
        for c in d["concepts"]:
            ref = f"{lv}:{c['id']}"
            i = verdict.get(ref)
            if i is None:
                keep.append(c)
                continue
            counts[i["verdict"]] += 1
            if i["verdict"] == "accept":
                if lowered and len(i.get("families") or []) < confirmation:
                    c["status"] = "hypothesis"   # accepted, but nothing independent stands behind it yet
                    unconfirmed += 1
                else:
                    c["status"] = "asserted"
            elif i["verdict"] == "reject":
                rejected.add(ref)
                continue
            else:
                c["status"] = "hypothesis"
                c["note"] = (c.get("note", "") + f" Review ({by}, {run.id}): {i['comment']}").strip()
            keep.append(c)
        d["concepts"] = keep
        if "relations" in d:
            d["relations"] = [r for r in d["relations"]
                              if f"{lv}:{r['from']}" not in rejected and f"{lv}:{r['to']}" not in rejected]
        save_json(f, d)
    unbound = 0
    app_dir = run.cfg.path("application")
    for f in sorted(app_dir.glob("*.json")):
        d = json.loads(f.read_text())
        ch = False
        for c in d["concepts"]:
            n = len(c.get("maps_to", []))
            c["maps_to"] = [m for m in c.get("maps_to", []) if m.get("target") not in rejected]
            if len(c["maps_to"]) != n:
                ch = True
                unbound += n - len(c["maps_to"])
        if ch:
            save_json(f, d)
    covers = {str(f.relative_to(run.cfg.root)): framing.sha256(f) for f in shared_files(run.cfg).values()}
    note = f"run {run.id}" + (f"; reviewer effort {effort}" if effort else "")
    entry = framing.approve(run.cfg, "review", p, by=by, at=now(), note=note, covers=covers)
    return {**counts, "accepted_unconfirmed": unconfirmed, "mappings_removed": unbound, "gate": entry}


def require_review(cfg) -> tuple[bool, str]:
    """`release` of a shared level: the latest review gate must match its review.yaml and the shared-level
    files as they were when the owner's verdicts were applied."""
    hits = [g for g in framing.gates(cfg) if g.get("gate") == "review"]
    if not hits:
        return False, "no review recorded (stage 8: `ontologizer review build`, then `review apply --by MODEL --effort E`)"
    last = hits[-1]
    ok, why = framing.gate_status(cfg, "review", cfg.root / last["file"])
    if not ok:
        return ok, why
    for rel, h in (last.get("covers") or {}).items():
        f = cfg.root / rel
        if not f.is_file() or framing.sha256(f) != h:
            return False, f"{rel} changed after the review by {last['by']} at {last['at']}; review it again"
    if not last.get("covers"):
        return False, "the review gate does not record the shared-level files it covers; run `review apply` again"
    return True, why


# ---- gold set ------------------------------------------------------------------
def _item(e: dict) -> dict | None:
    kind, _ = STAGE_KIND.get(e["stage"], (None, None))
    if kind is None or (e.get("answer") or {}).get("value") is None:
        return None
    q = e["question"]
    st = e.get("state") or {}
    n = q["id"][1:] if q["id"][:1] in "pcek" and q["id"][1:].isdigit() else None
    ctx: dict
    if kind == "evidence":
        ev = next((x for x in st.get("evidence", []) if str(x.get("n")) == n), {})
        ctx = {"claim": st.get("claim"), "path": ev.get("path"), "lines": ev.get("lines"), "text": ev.get("text")}
        ask = "Do these lines support the claim? label: true | false"
    elif kind in ("same-concept", "group-same-concept", "compare-same-concept"):
        pair = next((x for x in st.get("pairs", []) if str(x.get("n")) == n), {})
        ctx = {"first": pair.get("first"), "second": pair.get("second")}
        ask = "Are both the same concept? label: true | false"
    elif kind == "category":
        # stage 4 asks one concept per state (`concept`, question `category`) or a batch
        # (`concepts`, questions `k<n>`)
        concept = st.get("concept")
        if concept is None and n is not None:
            concept = next((x for x in st.get("concepts", []) if str(x.get("n")) == n), None)
        ctx = {"concept": concept}
        ask = f"Which category fits? label: one of {sorted(q.get('options', {}))}"
    else:
        cand = next((x for x in st.get("candidates", []) if str(x.get("n")) == n), {})
        ctx = {"candidate": cand}
        ask = f"What is it? label: one of {sorted(q.get('options', {}))}"
    out = {"key": e["key"], "stage": e["stage"], "kind": kind, "ask": ask, "context": ctx, "label": None}
    if kind == "triage":
        out["_answer"] = (e.get("answer") or {}).get("value") == "architectural"   # stratum only; not written
    return out


def sample(run: Run, name: str, size: int | None = None, seed: int = 0, kinds: list[str] | None = None) -> dict:
    size = size or int(run.process.get("gold_size", 80))
    entries = [e for e in run.log_entries() if e["mode"] == "jev"] or [e for e in run.log_entries() if e["mode"] == "llm"]
    by_kind: dict[str, list[dict]] = {}
    seen = set()
    for e in entries:
        it = _item(e)
        if it and it["key"] not in seen:
            seen.add(it["key"])
            by_kind.setdefault(it["kind"], []).append(it)
    rnd = random.Random(seed)
    want = kinds
    kinds = [k for k in ("evidence", "category", "same-concept", "group-same-concept", "compare-same-concept", "triage")
             if by_kind.get(k) and (not want or k in want)]
    picked = []
    if kinds:
        per = max(1, size // len(kinds))
        for k in kinds:
            pool = sorted(by_kind[k], key=lambda i: i["key"])
            rnd.shuffle(pool)
            if k == "triage":   # stratified by the decision's answer, so rare classes reach the gold set
                strata: dict[str, list] = {}
                for it in pool:
                    strata.setdefault(str(it.pop("_answer", None)), []).append(it)
                take = max(1, per // max(1, len(strata)))
                got = [it for v in strata.values() for it in v[:take]]
                rest = [it for v in strata.values() for it in v[take:]]
                picked += (got + rest)[:per]
            else:
                picked += pool[:per]
    for it in picked:
        it.pop("_answer", None)
    rnd.shuffle(picked)
    out = {"run": run.id, "sampled": now(), "seed": seed, "size": len(picked),
           "instructions": "Label every item: `label` true/false, or the category / triage class, from its "
                           "`context` (read the source when the context is not enough). Do not look up the model's "
                           "answer. The labeller is a strong agent that made none of these decisions; record it at the "
                           "top as `labelled_by: {model, effort, harness}`. Then run `ontologizer gold calibrate "
                           "gold/<name>.yaml`.",
           "labelled_by": None,
           "items": picked}
    p = run.cfg.root / "gold" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        raise ReviewError(f"{p} exists; choose another name (labels are never overwritten)")
    p.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120))
    return {"path": str(p), "size": len(picked), "by_kind": {k: min(len(by_kind[k]), max(1, size // len(kinds)))
                                                              for k in kinds} if kinds else {}}


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if str(v).lower() in ("true", "yes", "1"):
        return True
    if str(v).lower() in ("false", "no", "0"):
        return False
    return None


def _best(pts: list[tuple[float, bool]]) -> float:
    return max(GRID, key=lambda t: (sum((p >= t) == y for p, y in pts), -abs(t - 0.5)))


MIN_POSITIVES = 10   # architectural labels needed before the triage loss bound means anything


def _best_bounded(pts: list[tuple[float, bool]], max_loss: float, w: list[float] | None = None) -> float:
    """The most accurate triage threshold that loses at most `max_loss` of the architectural items
    (labelled true but below the threshold), items weighted by `w` (default 1); the lowest grid
    value when none qualifies."""
    w = w or [1.0] * len(pts)
    pos = sum(wi for wi, (_, y) in zip(w, pts) if y)
    ok = [t for t in GRID if not pos or sum(wi for wi, (p, y) in zip(w, pts) if y and p < t) / pos <= max_loss]
    if not ok:
        return GRID[0]
    return max(ok, key=lambda t: (sum(wi for wi, (p, y) in zip(w, pts) if (p >= t) == y), -abs(t - 0.5)))


def _loo(pts: list[tuple[float, bool]]) -> float | None:
    """Accuracy of a threshold chosen without the item it is tested on (the honest number)."""
    if len(pts) < 2:
        return None
    hits = sum((p >= _best(pts[:i] + pts[i + 1:])) == y for i, (p, y) in enumerate(pts))
    return round(hits / len(pts), 3)


def calibrate(run: Run, gold_file: str | Path | list) -> dict:
    files = [Path(x) for x in (gold_file if isinstance(gold_file, (list, tuple)) else [gold_file])]
    log: dict[str, dict] = {}
    for e in run.log_entries(with_state=False):
        if e["mode"] == "jev" or e["key"] not in log:
            log[e["key"]] = e
    labelled, labellers, seen = [], [], set()
    for f in files:
        g = yaml.safe_load(f.read_text()) or {}
        items = [i for i in g.get("items", []) if i.get("label") is not None and i["key"] not in seen]
        if not items:
            raise ReviewError(f"{f}: no labelled items")
        lb = g.get("labelled_by") or {}
        if not lb.get("model"):
            raise ReviewError(f"{f}: `labelled_by: {{model, effort}}` required: who labelled the gold set")
        authors = sorted({log[i["key"]].get("model") for i in items if i["key"] in log} - {None})
        if lb["model"] in authors:
            raise ReviewError(f"{f}: labelled by {lb['model']}, which made some of these decisions ({authors}); "
                              f"the labeller must be another model")
        seen |= {i["key"] for i in items}
        labelled += items
        labellers.append(lb)
    lb = labellers[0] if len({json.dumps(x, sort_keys=True) for x in labellers}) == 1 else {"model": "+".join(
        sorted({x["model"] for x in labellers})), "files": labellers}
    max_loss = float((run.decisions_conf or {}).get("triage_max_loss", 0.03))
    thresholds, accuracy, n = {}, {}, {}
    for kind_thr in sorted({v for v in STAGE_KIND.values()}, key=str):
        kind, thr_name = kind_thr
        rows = [(i, log[i["key"]]) for i in labelled if i["kind"] == kind and i["key"] in log]
        if not rows:
            continue
        if kind in ("evidence", "same-concept", "group-same-concept", "compare-same-concept"):
            pts = [(float(e["answer"]["value"]), _bool(i["label"])) for i, e in rows if _bool(i["label"]) is not None]
            if not pts:
                continue
            best = _best(pts)
            thresholds[thr_name] = best
            accuracy[thr_name] = {"in_sample": round(sum((p >= best) == y for p, y in pts) / len(pts), 3),
                                  "leave_one_out": _loo(pts)}
            n[thr_name] = len(pts)
        elif kind == "triage":
            pts = [(e["answer"], str(i["label"])) for i, e in rows]
            # the sample is stratified by Jev's answer; weight each item by its stratum's share of the run
            pop: dict[str, int] = {}
            for e in log.values():
                if e["stage"] == "inventory" and (e.get("answer") or {}).get("value") is not None:
                    pop[e["answer"]["value"]] = pop.get(e["answer"]["value"], 0) + 1
            smp: dict[str, int] = {}
            for a, _ in pts:
                smp[a.get("value")] = smp.get(a.get("value"), 0) + 1
            w = [pop.get(a.get("value"), 0) / smp[a.get("value")] for a, _ in pts]
            tot = sum(w) or 1.0
            accuracy["triage"] = round(sum(wi for wi, (a, y) in zip(w, pts) if a.get("value") == y) / tot, 3)
            n["triage"] = len(pts)
            arch = [((a.get("probabilities") or {}).get("architectural", 1.0 if a.get("value") == "architectural" else 0.0),
                     y == "architectural") for a, y in pts]
            n_arch = sum(1 for _, y in arch if y)
            n["triage_keep"] = n_arch
            if n_arch < MIN_POSITIVES:
                accuracy["triage_keep"] = {"skipped": f"{n_arch} architectural labels; at least {MIN_POSITIVES} "
                                                      f"needed to bound the loss"}
                continue
            best = _best_bounded(arch, max_loss, w)
            thresholds["triage_keep"] = best
            wa = sum(wi for wi, (_, y) in zip(w, arch) if y) or 1.0
            lost = sum(wi for wi, (p, y) in zip(w, arch) if y and p < best) / wa
            accuracy["triage_keep"] = {"weighted": round(sum(wi for wi, (p, y) in zip(w, arch) if (p >= best) == y)
                                                         / tot, 3),
                                       "architectural_lost": round(lost, 3), "max_loss": max_loss,
                                       "architectural_labels": n_arch}
        else:
            pts = [(e["answer"].get("value"), str(i["label"])) for i, e in rows]
            accuracy["category"] = round(sum(a == y for a, y in pts) / len(pts), 3)
            n["category"] = len(pts)
    prov = sorted(k for k in thresholds if n.get(k, 0) < 20)
    out = {"calibrated": now(), "run": run.id, "gold": [f.name for f in files], "labelled_by": lb,
           "thresholds": thresholds,
           "accuracy": accuracy, "n": n, "provisional": prov if thresholds else True}
    p = run.cfg.root / (run.process.get("calibration") or "calibration.yaml")
    p.write_text("# Written by `ontologizer gold calibrate`; thresholds override `decisions.thresholds`.\n"
                 + yaml.safe_dump(out, sort_keys=False, allow_unicode=True))
    for f, x in zip(files, labellers):
        gf = f.resolve()
        if gf.is_relative_to(run.cfg.root.resolve()):
            framing.approve(run.cfg, "gold", gf, by=x["model"], at=now(),
                            note=f"run {run.id}; labeller effort {x.get('effort') or '?'}")
    return out
