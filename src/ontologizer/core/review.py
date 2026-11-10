"""Stage 8: review, gold set and calibration (docs/process.md §8).

- `review build` lists every domain and task concept (no cap) with its
  support, families, stability, the verification share of its supporting
  concepts and their evidence, and every relation of the shared levels, for
  a strong agent to mark accept / reject / revise. A verdict stands only while
  what it judged is unchanged, so after an edit only the changes need one;
  `--since RUN` carries accepted verdicts over from an earlier run. A
  carried verdict whose support changed says so (`support_changed`), and
  supporting concepts verified in an earlier run show that run's result.
- `review apply` makes accepted concepts `asserted`, removes rejected ones
  (and the project mappings to them), notes revisions, marks as `disputed`
  what an open reading or a declared dispute holds apart, and records the
  `review` gate; `release` of a shared level requires that gate.
- `gold sample` draws a stratified sample of logged decisions (evidence
  claims, categories, same-concept pairs) for a strong agent to label, without
  showing the model's answer.
- `gold calibrate` compares the logged answers with the labels, chooses the
  thresholds that maximise accuracy and writes them with the measured
  accuracy to `calibration.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import re
import random
from pathlib import Path

import yaml

from ontologizer.core import frame as framing
from ontologizer.core.frame import Frame
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import application_docs, carry_dispute, disputes, shared_files

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


def _runs_dir(cfg) -> Path:
    return cfg.root / (cfg.data.get("process") or {}).get("runs", "runs")


def _earlier_runs(run: Run, since: str | None) -> list[Run]:
    """`since`, then the run its review was built `since`, and so on: where earlier verification lives."""
    out, seen, rid = [], {run.id}, since
    while rid and rid not in seen and len(out) < 50:
        seen.add(rid)
        r = Run(run.cfg, rid)
        out.append(r)
        q = r.path("review.yaml")
        rid = (yaml.safe_load(q.read_text()) or {}).get("since") if q.is_file() else None
    return out


CHECKED = ("supported", "unsupported", "missing")


def _verification(runs: list[Run], cache: dict, project: str, pc: dict) -> dict:
    """The stage 6 result for a supporting concept: from this run (`runs[0]`) or, when this run did not check
    it, from the latest earlier run that checked the evidence it has now. A concept that stage 6 removed and that
    is back in the application file is `restored`, with the result that removed it in `verified_before`."""
    from ontologizer.core.verify import final_items
    claim = f"concept:{pc['id']}"
    now_ev = {(e.get("path"), str(e.get("lines") or "")) for e in pc.get("evidence", [])}
    for k, r in enumerate(runs):
        if (r.id, project) not in cache:
            v = load_json(r.path("verify", f"{project}.json"), {}) or {}
            cache[(r.id, project)] = (v, final_items(r, project, v) if v else [])
        v, fin = cache[(r.id, project)]
        items = [i for i in fin if i["claim"] == claim]
        vi = [i for i in items if i["final"] in CHECKED]
        ok = sum(1 for i in vi if i["final"] == "supported")
        share = f"{ok}/{len(vi)}" if vi else "unchecked"
        if claim in ((v.get("applied") or {}).get("removed_claims") or []):
            return {"verified": "restored", "verified_before": share, "verified_in": r.id}
        if not vi:
            continue
        if k == 0:
            return {"verified": share}
        if not now_ev or not now_ev <= {(i.get("path"), str(i.get("lines") or "")) for i in items}:
            break   # that run checked other evidence than the concept has now
        return {"verified": share, "verified_in": r.id}
    return {"verified": "unchecked"}


def build(run: Run, frame: Frame, since: str | None = None) -> dict:
    """`since`: an earlier run whose accepted verdicts stand for whatever has not changed since."""
    st = load_json(run.path("stability.json"), {}) or {}
    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    if since and not (_runs_dir(run.cfg) / since / "review.yaml").is_file():
        raise ReviewError(f"--since {since}: no review at {_runs_dir(run.cfg) / since / 'review.yaml'}")
    runs = [run] + _earlier_runs(run, since)
    cache: dict = {}
    held = disputes(run.cfg, docs)
    items = []
    for lv, f in shared_files(run.cfg).items():
        for c in json.loads(f.read_text()).get("concepts", []):
            ref = f"{lv}:{c['id']}"
            sup = []
            for p, d in docs.items():
                for pc in d["concepts"]:
                    if any(m.get("target") == ref for m in pc.get("maps_to", [])):
                        sup.append({"concept": f"{p}:{pc['id']}", "label": pc["label"],
                                    "category": pc.get("upper_category"),
                                    "kind": next(m["kind"] for m in pc["maps_to"] if m.get("target") == ref),
                                    **_verification(runs, cache, p, pc),
                                    "evidence": [f"{e['path']}:{e.get('lines', '')}" for e in pc.get("evidence", [])[:2]]})
            s = (st.get("concepts") or {}).get(ref, {})
            items.append({"ref": ref, "label": c["label"], "category": c["upper_category"],
                          "category_differs": sorted({x["category"] for x in sup if x["category"] != c["upper_category"]}),
                          "definition": c["definition"], "status": c.get("status", "hypothesis"),
                          "families": s.get("families", []), "stability": s.get("mark", "unknown"),
                          "breaks_without": s.get("breaks_without", []), "reason": (c.get("support") or {}).get("reason"),
                          "support": sup, "verdict": None, "comment": None})
            if ref in held:
                e = held[ref]
                items[-1]["readings"] = {"open": e["open"], "settled": e["settled"], "declared": e["declared"]}
    for i in items:
        i["fingerprint"] = _fingerprint(i)
    relations = _relation_items(run, docs)
    doc = {"run": run.id, "built": now(), **({"since": since} if since else {}),
           "instructions": "Set `verdict` of every item and relation with an empty one to accept, reject or revise "
                           "(with `comment`; `revise` keeps a relation unchanged, so reject one that is wrong), "
                           "then record the gate with `ontologizer review apply --by <name>`. A shared concept whose "
                           "`category_differs` lists categories has a supporting project concept typed otherwise: "
                           "keep the shared category only for a reason you state. A relation with `counterpart: "
                           "false` has no project relation of its type and direction between the concepts behind "
                           "its ends (`counterpart_other` lists the ones of another type or direction), so check it "
                           "in the source. An item with `support_changed` keeps a verdict given on other support "
                           "(`judged_support`): look at what was added or removed, change the verdict if it no longer "
                           "holds, then delete `support_changed`. A verdict with `carried_from` comes from an earlier "
                           "run; set it to null to reopen it, and a verdict or comment you change is your own. "
                           "`verified` is the stage 6 result of a supporting concept (`verified_in`: the earlier run "
                           "that checked it); `restored` means stage 6 removed it and it was put back afterwards "
                           "(`verified_before`: the result that removed it), so check its evidence in the source. "
                           "`readings` lists the partial or disputed concepts behind a shared one: an open reading "
                           "or a declared dispute makes it `disputed` whatever the verdict, until a consolidation "
                           "file settles it with a reason.",
           "items": items, "relations": relations}
    p = review_path(run)
    p.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    if p.is_file():   # verdicts stand for what has not changed since they were given
        kept = _carry(_with_carried_as(run.cfg, yaml.safe_load(p.read_text()) or {}), items, relations)
    carried = 0
    if since:
        q = _runs_dir(run.cfg) / since / "review.yaml"
        carried = _carry(yaml.safe_load(q.read_text()) or {}, items, relations, accepted_only=True, source=since)
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
    open_ = sum(1 for i in items + relations if not i.get("verdict"))
    changed = [{"ref": i["ref"], **i["support_changed"]} for i in items if i.get("support_changed")]
    return {"items": len(items), "relations": len(relations), "open": open_, "kept": kept, "carried": carried,
            "support_changed": changed, "reopened": sum(1 for i in items + relations if i.get("reopened")),
            "path": str(p)}


def _fingerprint(i: dict) -> str:
    """What a verdict was given on. A verdict does not outlive a change to it."""
    if "from" in i:
        what = [i["ref"], i.get("label")]
    else:
        what = [i["ref"], i["label"], i["category"], i["definition"]]
    return hashlib.sha256(json.dumps(what, ensure_ascii=False).encode()).hexdigest()[:16]


def _carry(old: dict, items: list[dict], relations: list[dict], accepted_only: bool = False,
           source: str | None = None) -> int:
    def same(o: dict, i: dict) -> bool:
        if o.get("fingerprint"):
            return o["fingerprint"] == i["fingerprint"]
        return (o.get("definition"), o.get("category")) == (i.get("definition"), i.get("category"))  # an older file

    n = 0
    for mine, theirs in ((items, old.get("items", [])), (relations, old.get("relations", []))):
        by_ref = {x["ref"]: x for x in theirs}
        for i in mine:
            o = by_ref.get(i["ref"])
            if i.get("verdict") or not o or not same(o, i):
                continue
            if not o.get("verdict"):
                if source is None and (o.get("carried_from") or o.get("reopened")):
                    i["reopened"] = o.get("reopened") or o["carried_from"]   # a reviewer reopened a carried verdict
                continue
            if source and i.get("reopened"):
                continue   # reopened: carrying it again would undo the reopening
            if accepted_only and o["verdict"] != "accept":
                continue
            i["verdict"], i["comment"] = o["verdict"], o.get("comment")
            i.pop("reopened", None)
            if source:
                i["carried_from"], i["carried_as"] = source, {"verdict": o["verdict"], "comment": o.get("comment")}
            elif is_carried(o):   # still the carried verdict; one the reviewer changed is the reviewer's own
                i["carried_from"] = o["carried_from"]
                if "carried_as" in o:
                    i["carried_as"] = o["carried_as"]
            if "support" in i:
                _mark_support(o, i)
            n += 1
    return n


def _with_carried_as(cfg, old: dict) -> dict:
    """A file written before `carried_as` existed: read what each carried verdict was from the review it came from,
    so a comment the reviewer has since changed counts as the reviewer's own."""
    cache: dict[str, dict] = {}
    for x in old.get("items", []) + old.get("relations", []):
        src = x.get("carried_from")
        if not src or "carried_as" in x:
            continue
        if src not in cache:
            q = _runs_dir(cfg) / src / "review.yaml"
            d = (yaml.safe_load(q.read_text()) or {}) if q.is_file() else {}
            cache[src] = {y["ref"]: y for y in d.get("items", []) + d.get("relations", [])}
        o = cache[src].get(x["ref"])
        if o is not None:
            x["carried_as"] = {"verdict": o.get("verdict"), "comment": o.get("comment")}
    return old


def is_carried(i: dict) -> bool:
    """A verdict carried from an earlier run that the reviewer has not changed since (verdict and comment)."""
    if not i.get("carried_from"):
        return False
    was = i.get("carried_as")
    return was is None or (was.get("verdict"), was.get("comment")) == (i.get("verdict"), i.get("comment"))


def _support_refs(i: dict) -> list[str]:
    return sorted(s["concept"] for s in i.get("support") or [])


def _mark_support(o: dict, i: dict) -> None:
    """A verdict outlives a change of support (new evidence would otherwise reset every verdict on each update),
    but the item says what it was judged on and what changed, until the reviewer deletes `support_changed`."""
    if "support" not in o and "judged_support" not in o:
        return   # an older file: nothing to compare with
    keep = o.get("support_changed") and "judged_support" in o and (not o.get("carried_from") or is_carried(o))
    judged = o["judged_support"] if keep else _support_refs(o)
    now_ = _support_refs(i)
    if judged != now_:
        i["judged_support"] = judged
        i["support_changed"] = {"added": sorted(set(now_) - set(judged)), "removed": sorted(set(judged) - set(now_))}


def _upward(start: str, parents: dict[str, set[str]]) -> set[str]:
    out, todo = {start}, [start]
    while todo:
        for q in parents.get(todo.pop(), ()):
            if q not in out:
                out.add(q)
                todo.append(q)
    return out


def _relation_items(run: Run, docs: dict[str, dict]) -> list[dict]:
    """The relations of the shared levels, each with whether a project relation stands behind it.

    A project relation `x --t--> y` is a counterpart of the shared relation `A --t--> B` when it has the same type
    `t`, goes in the same direction, and `x` stands for `A` and `y` for `B`. A project concept stands for a shared
    concept that it, or a project concept it is narrower than (`broader`, any number of steps), maps to, and for
    every shared concept that one specializes in turn (`broader`, or a `specialization` mapping between shared
    concepts, any number of steps): a relation between narrower concepts instantiates the relation between the
    wider ones, never the reverse. A project end written as a shared reference stands for it directly."""
    sfiles = shared_files(run.cfg)
    shared_up: dict[str, set[str]] = {}
    for lv, f in sfiles.items():
        for c in json.loads(f.read_text()).get("concepts", []):
            ref = f"{lv}:{c['id']}"
            ups = shared_up.setdefault(ref, set())
            ups |= {_ref(lv, b) for b in c.get("broader", [])}
            ups |= {m["target"] for m in c.get("maps_to", []) if m.get("target") and m.get("kind") == "specialization"}
    edges: list[tuple[str, str, str, str, set[str], set[str]]] = []
    for p, d in docs.items():
        parents = {pc["id"]: set(pc.get("broader", [])) for pc in d["concepts"]}
        maps = {pc["id"]: {m["target"] for m in pc.get("maps_to", []) if m.get("target")} for pc in d["concepts"]}
        memo: dict[str, set[str]] = {}

        def stands(x: str) -> set[str]:
            if x not in memo:
                direct = {x} if x in shared_up else set().union(*(maps.get(y, set()) for y in _upward(x, parents)))
                memo[x] = set().union(set(), *(_upward(t, shared_up) for t in direct))
            return memo[x]
        for r in d.get("relations", []):
            edges.append((p, r["from"], r["type"], r["to"], stands(r["from"]), stands(r["to"])))
    out = []
    for lv, f in sfiles.items():
        for r in json.loads(f.read_text()).get("relations", []):
            a, b = _ref(lv, r["from"]), _ref(lv, r["to"])
            seen = any(t == r["type"] and a in sa and b in sb for _, _, t, _, sa, sb in edges)
            item = {"ref": f"{a}--{r['type']}--{b}", "from": a, "type": r["type"], "to": b, "label": r.get("label"),
                    "counterpart": seen, "verdict": None, "comment": None}
            if not seen:
                other = sorted({f"{p}: {x} --{t}--> {y}" for p, x, t, y, sa, sb in edges
                                if (a in sa and b in sb) or (b in sa and a in sb)})
                if other:
                    item["counterpart_other"] = other
            item["fingerprint"] = _fingerprint(item)
            out.append(item)
    return out


def _ref(level: str, end: str) -> str:
    return end if ":" in end else f"{level}:{end}"


def apply(run: Run, by: str, effort: str | None = None) -> dict:
    p = review_path(run)
    if not p.is_file():
        raise ReviewError("no review.yaml: run `ontologizer review build` first")
    doc = yaml.safe_load(p.read_text()) or {}
    items = doc.get("items", [])
    relations = doc.get("relations", [])
    missing = [i["ref"] for i in items + relations if i.get("verdict") not in VERDICTS]
    if missing:
        raise ReviewError(f"{len(missing)} item(s) without a verdict (accept|reject|revise): {missing[:10]}")
    bad = [i["ref"] for i in items + relations if i["verdict"] == "revise" and not i.get("comment")]
    if bad:
        raise ReviewError(f"revise needs a comment: {bad[:10]}")
    verdict = {i["ref"]: i for i in items}
    counts = {v: 0 for v in VERDICTS}
    from ontologizer.core.levels import Preset
    confirmation = Preset.load(run.cfg.preset_path).min_families
    fr = Frame.load(run.cfg)
    lowered = fr.admission_families(confirmation) < confirmation
    unconfirmed = 0
    rejected = set()
    held = disputes(run.cfg, application_docs(run.cfg, [x for x in fr.projects if x not in fr.overlay]))
    disputed, one_sided = 0, []
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
                if i.get("comment") and not is_carried(i):   # a carried comment reached the note in its own run
                    _note(c, by, run.id, i["comment"])
            elif i["verdict"] == "reject":
                rejected.add(ref)
                continue
            else:
                c["status"] = "hypothesis"
                _note(c, by, run.id, i["comment"])
            e = held.get(ref)
            if carry_dispute(c, e):
                disputed += 1
                if i["verdict"] == "accept" and not (lowered and len(i.get("families") or []) < confirmation):
                    unconfirmed += 1     # an accept that does not assert: it counts with those that stay hypotheses
            elif e and (e["open"] or e["declared"]):
                one_sided.append(ref)
            keep.append(c)
        d["concepts"] = keep
        save_json(f, d)
    gone_rel = {r["ref"] for r in relations if r["verdict"] == "reject"}
    for lv, f in shared_files(run.cfg).items():   # after the loop above: a relation may reach another level
        d = json.loads(f.read_text())
        if "relations" in d:
            d["relations"] = [r for r in d["relations"]
                              if _ref(lv, r["from"]) not in rejected and _ref(lv, r["to"]) not in rejected
                              and f"{_ref(lv, r['from'])}--{r['type']}--{_ref(lv, r['to'])}" not in gone_rel]
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
    carried = sum(1 for i in items + relations if is_carried(i))
    changed = sum(1 for i in items if i.get("support_changed"))
    note = f"run {run.id}" + (f"; reviewer effort {effort}" if effort else "") \
        + (f"; {carried} verdicts carried from an earlier review" if carried else "") \
        + (f"; {changed} verdicts kept although their support changed" if changed else "")
    entry = framing.approve(run.cfg, "review", p, by=by, at=now(), note=note, covers=covers)
    return {**counts, "accepted_unconfirmed": unconfirmed, "disputed": disputed, "one_sided": one_sided,
            "mappings_removed": unbound, "gate": entry, "relations_rejected": len(gone_rel), "carried": carried,
            "support_changed": changed}


def _note(c: dict, by: str, run_id: str, comment: str) -> None:
    """Append a review remark to a concept's note once: applying the same review again adds nothing, whoever
    signs the second apply (the remark is the run's comment, not the signature)."""
    note = c.get("note", "")
    if re.search(r"Review \([^()]*, " + re.escape(run_id) + r"\): " + re.escape(comment), note):
        return
    remark = f"Review ({by}, {run_id}): {comment}"
    # a comment of the same run that a reviewer has since extended replaces its earlier form, so it is not kept twice
    for m in re.finditer(r"Review \([^()]*, " + re.escape(run_id) + r"\): (.*?)(?= Review \(|$)", note, re.S):
        if m.group(1) and comment.startswith(m.group(1).rstrip()):
            c["note"] = note[:m.start()] + remark + note[m.end():]
            return
    c["note"] = (note + " " + remark).strip()


def require_review(cfg) -> tuple[bool, str]:
    """`release` of a shared level: the latest review gate must match its review.yaml and the shared-level
    files as they were when the verdicts were applied."""
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
