"""Stage 3: reconcile the independent extractions of one project (docs/process.md §3).

1. Code proposes candidate pairs between every two extractions (shared
   inventory ids, shared evidence files, label similarity).
2. The decision port judges each candidate pair (`noul`: the same concept?);
   with decisions off, a pair is the same when a label or alt label is equal.
3. Code matches one-to-one by probability and builds clusters across extractors.
4. Agreement per extractor pair = matched ÷ (|A| + |B| − matched).
5. Disputes: a concept only one extraction has, or a cluster whose members
   disagree on the gUFO category. An LLM resolves each in
   `reconcile/<p>.resolutions.json` with a reason; `--apply` builds
   `application/<p>.json` from the clusters and the resolutions.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from ontologizer.core.brief import extractions
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.similarity import pair_score, same_label
from ontologizer.ports.decisions import Question

TOP_K = 3
MIN_SCORE = 0.15
PAIR_BATCH = 20


class ReconcileError(ValueError):
    pass


def _brief(c: dict) -> dict:
    return {"label": c.get("label"), "alt_labels": c.get("alt_labels", []), "definition": c.get("definition"),
            "category": c.get("upper_category"), "evidence": sorted({e.get("path") for e in c.get("evidence", [])})[:5]}


def candidate_pairs(a: list[dict], b: list[dict]) -> list[tuple[int, int, float]]:
    out = {}
    for i, x in enumerate(a):
        scored = sorted(((pair_score(x, y), j) for j, y in enumerate(b)), reverse=True)
        for s, j in scored[:TOP_K]:
            if s >= MIN_SCORE or same_label(x, b[j]):
                out[(i, j)] = s
    for j, y in enumerate(b):
        scored = sorted(((pair_score(x, y), i) for i, x in enumerate(a)), reverse=True)
        for s, i in scored[:TOP_K]:
            if s >= MIN_SCORE or same_label(a[i], y):
                out[(i, j)] = s
    return sorted(((i, j, s) for (i, j), s in out.items()), key=lambda t: (-t[2], t[0], t[1]))


def judge(run: Run, project: str, ea: str, a: list[dict], eb: str, b: list[dict],
          pairs: list[tuple[int, int, float]], dec) -> list[dict]:
    judged = []
    if not dec.on:
        for i, j, s in pairs:
            same = same_label(a[i], b[j])
            judged.append({"a": a[i]["id"], "b": b[j]["id"], "score": round(s, 3), "p": 1.0 if same else 0.0,
                           "mode": "off"})
        return judged
    for k in range(0, len(pairs), PAIR_BATCH):
        chunk = pairs[k:k + PAIR_BATCH]
        state = {"task": f"Two independent extractions of the software project {project!r} named its concepts. "
                         "Decide for each pair whether both entries denote the same concept of the project.",
                 "pairs": [{"n": n, "first": _brief(a[i]), "second": _brief(b[j])} for n, (i, j, _) in enumerate(chunk)]}
        qs = [Question(id=f"p{n}", type="noul",
                       instructions=f"Pair n={n}: do 'first' and 'second' denote the same concept of the project "
                                    f"(same thing under possibly different names), not merely related ones?")
              for n in range(len(chunk))]
        ans = dec.ask(state, qs)
        for n, (i, j, s) in enumerate(chunk):
            x = ans.get(f"p{n}")
            if x is not None and x.ok:
                judged.append({"a": a[i]["id"], "b": b[j]["id"], "score": round(s, 3), "p": round(float(x.value), 3),
                               "mode": dec.mode})
    return judged


def match(judged: list[dict], threshold: float) -> list[tuple[str, str, float]]:
    used_a, used_b, out = set(), set(), []
    for x in sorted(judged, key=lambda d: (-d["p"], -d["score"], d["a"], d["b"])):
        if x["p"] >= threshold and x["a"] not in used_a and x["b"] not in used_b:
            used_a.add(x["a"])
            used_b.add(x["b"])
            out.append((x["a"], x["b"], x["p"]))
    return out


def required_extractors(run: Run, project: str) -> list[dict]:
    """The extractors a project needs: every one not `optional`, and one with `min_candidates: N`
    when the project's inventory lists at least N candidates in total (a third extractor on large
    projects)."""
    inv = load_json(run.path("inventory", f"{project}.json"), {}) or {}
    total = inv.get("total") if inv.get("status") == "measured" else None
    out = []
    for e in run.extractors:
        if "min_candidates" in e:
            if total is not None and total >= int(e["min_candidates"]):
                out.append(e)
        elif not e.get("optional"):
            out.append(e)
    return out


def analyse(run: Run, project: str) -> dict:
    exs = extractions(run, project)
    if len(exs) < 2:
        raise ReconcileError(f"{project}: {len(exs)} extraction(s) in {run.path('extract', project)}; "
                             f"stage 3 needs at least two (extractors {[e['id'] for e in run.extractors]})")
    need = [e["id"] for e in required_extractors(run, project)]
    lacking = [e for e in need if e not in exs]
    if lacking:
        raise ReconcileError(f"{project}: extraction(s) {lacking} required here (process.extractors, "
                             f"`min_candidates`) are missing in {run.path('extract', project)}")
    order = [e["id"] for e in run.extractors if e["id"] in exs]
    dec = run.decider("reconcile")
    thr = run.threshold("same_concept")
    pairs_out, agreement = {}, {}
    # union-find over (extractor, id)
    parent: dict[tuple[str, str], tuple[str, str]] = {(e, c["id"]): (e, c["id"]) for e in order for c in exs[e]["concepts"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def members(root):
        return {k for k in parent if find(k) == root}

    for ea, eb in itertools.combinations(order, 2):
        a, b = exs[ea]["concepts"], exs[eb]["concepts"]
        judged = judge(run, project, ea, a, eb, b, candidate_pairs(a, b), dec)
        matched = match(judged, thr)
        for x, y, _ in matched:
            ra, rb = find((ea, x)), find((eb, y))
            if ra != rb and not ({e for e, _ in members(ra)} & {e for e, _ in members(rb)}):
                parent[rb] = ra
        n = len(matched)
        agreement[f"{ea}~{eb}"] = {"a": len(a), "b": len(b), "matched": n,
                                   "agreement": round(n / (len(a) + len(b) - n), 3) if (len(a) + len(b) - n) else 1.0}
        pairs_out[f"{ea}~{eb}"] = {"judged": judged, "matched": [list(m) for m in matched]}
    dec.done()

    by_ref = {(e, c["id"]): c for e in order for c in exs[e]["concepts"]}
    groups: dict[tuple, list] = {}
    for k in parent:
        groups.setdefault(find(k), []).append(k)
    clusters, disputes = [], []
    for root, mem in sorted(groups.items(), key=lambda kv: (order.index(min(kv[1], key=lambda m: order.index(m[0]))[0]), kv[0][1])):
        mem = sorted(mem, key=lambda m: (order.index(m[0]), m[1]))
        cid = f"c-{mem[0][0]}-{mem[0][1]}"
        cats = sorted({by_ref[m]["upper_category"] for m in mem})
        clusters.append({"id": cid, "members": [f"{e}:{i}" for e, i in mem], "categories": cats})
        if len({e for e, _ in mem}) < len(order):
            missing = [e for e in order if e not in {x for x, _ in mem}]
            d = {"id": f"d-{cid}", "type": "unmatched", "cluster": cid, "missing_in": missing,
                 "concepts": {f"{e}:{i}": by_ref[(e, i)] for e, i in mem}}
            if len(cats) > 1:
                d["categories"] = cats   # a keep must then also decide the category
            disputes.append(d)
        elif len(cats) > 1:
            disputes.append({"id": f"d-{cid}", "type": "category", "cluster": cid, "categories": cats,
                             "concepts": {f"{e}:{i}": by_ref[(e, i)] for e, i in mem}})
    mean = round(sum(v["agreement"] for v in agreement.values()) / len(agreement), 3)
    full = sum(1 for c in clusters if len({m.split(":", 1)[0] for m in c["members"]}) == len(order))
    agreement_all = round(full / len(clusters), 3) if clusters and len(order) > 2 else None
    doc = {"project": project, "run": run.id, "at": now(), "extractors": order, "mode": dec.mode,
           "threshold": thr, "agreement": agreement, "agreement_mean": mean, "agreement_all": agreement_all,
           "extractor_meta": {e: exs[e].get("extractor", {"id": e}) for e in order},
           "clusters": clusters, "disputes": disputes, "pairs": pairs_out,
           "resolutions_file": str(run.path("reconcile", f"{project}.resolutions.json")),
           "how_to_resolve": RESOLVE_HELP}
    save_json(run.path("reconcile", f"{project}.json"), doc)
    return doc


RESOLVE_HELP = (
    "Write <project>.resolutions.json as {\"model\": \"<model id>\", \"resolutions\": {\"<dispute id>\": {...}}}. "
    "For type unmatched: {\"action\": \"keep\" | \"drop\" | \"same_as\", \"target\": \"<cluster id>\" (same_as only; "
    "not a dropped cluster), \"upper_category\": \"...\" (keep, when the dispute lists `categories`), "
    "\"concept\": {<fields to override>} (optional), \"reason\": \"...\"}. "
    "For type category: {\"upper_category\": \"<category>\", \"concept\": {...} (optional), \"reason\": \"...\"}. "
    "Every dispute needs a resolution with a reason and `source`: [{\"path\", \"lines\", \"quote\"}], the lines you "
    "read at the analysed commit and a short exact quote from them (`--apply` checks that the quote is there).")


def _merge(members: list[dict]) -> dict:
    """The first extractor's concept, with the others' names, evidence and inventory ids added."""
    base = json.loads(json.dumps(members[0]))
    base.pop("maps_to", None)
    alts = list(base.get("alt_labels", []))
    seen_ev = {(e.get("path"), str(e.get("lines"))) for e in base.get("evidence", [])}
    inv = list(base.get("inventory", []))
    for m in members[1:]:
        for name in [m.get("label"), *m.get("alt_labels", [])]:
            if name and name != base["label"] and name not in alts:
                alts.append(name)
        for e in m.get("evidence", []):
            k = (e.get("path"), str(e.get("lines")))
            if k not in seen_ev:
                seen_ev.add(k)
                base.setdefault("evidence", []).append(e)
        inv += [x for x in m.get("inventory", []) if x not in inv]
    if alts:
        base["alt_labels"] = alts
    if inv:
        base["inventory"] = inv
    return base


def _norm_ws(t: str) -> str:
    return " ".join(str(t).split()).lower()


def check_sources(run: Run, project: str, rec: dict, res: dict, exs: dict) -> list[str]:
    """Each resolution cites the source lines its resolver read: `source: [{path, lines, quote}]`; the
    quote (whitespace and case aside) must be found at those lines of the analysed commit. Returns
    the problems; `process.resolution_sources: false` turns the check off (earlier runs)."""
    if run.process.get("resolution_sources", True) is False:
        return []
    from ontologizer.core.sources import source_info
    from ontologizer.core.verify import UNKNOWN_COMMIT, read_at
    info = source_info(run.cfg, project)
    root = Path(info["dir"]) if info["dir"] else None
    commit = next((d.get("source", {}).get("commit") for d in exs.values() if d.get("source", {}).get("commit")),
                  info["commit"])
    out = []
    for d in rec["disputes"]:
        srcs = (res.get(d["id"]) or {}).get("source") or []
        if not srcs:
            out.append(f"{d['id']}: no `source`")
            continue
        ok = False
        for x in srcs:
            if not (x.get("path") and x.get("quote")):
                continue
            text = read_at(root, commit, x["path"], str(x.get("lines")) if x.get("lines") else None)
            if text is UNKNOWN_COMMIT:
                out.append(f"{d['id']}: source checkout lacks commit {commit}; cannot check the quote")
                ok = True
                break
            if text is not None and _norm_ws(x["quote"]) in _norm_ws(text):
                ok = True
                break
        if not ok:
            out.append(f"{d['id']}: quote not found at the cited lines")
    return out


def apply(run: Run, project: str, out: Path) -> dict:
    rec = load_json(run.path("reconcile", f"{project}.json"))
    if rec is None:
        raise ReconcileError(f"{project}: run `ontologizer reconcile {project}` first")
    res_doc = load_json(run.path("reconcile", f"{project}.resolutions.json"), {}) or {}
    res = res_doc.get("resolutions") or {}
    missing = [d["id"] for d in rec["disputes"] if not (res.get(d["id"]) or {}).get("reason")]
    if missing:
        raise ReconcileError(f"{project}: {len(missing)} dispute(s) without a resolution and reason in "
                             f"{run.path('reconcile', project + '.resolutions.json')}: {missing[:10]}")
    exs = extractions(run, project)
    order = rec["extractors"]
    unread = check_sources(run, project, rec, res, exs)
    if unread:
        raise ReconcileError(f"{project}: {len(unread)} resolution(s) without source lines read: "
                             + "; ".join(unread[:8]) + (f" (+{len(unread) - 8})" if len(unread) > 8 else ""))
    by_ref = {f"{e}:{c['id']}": c for e in order for c in exs[e]["concepts"]}
    from ontologizer.core.levels import Preset
    cats_ok = set(Preset.load(run.cfg.preset_path).categories)
    clusters = {c["id"]: list(c["members"]) for c in rec["clusters"]}
    drop: set[str] = set()
    same_as: dict[str, str] = {}
    overrides: dict[str, dict] = {}
    for d in rec["disputes"]:
        r = res[d["id"]]
        cid = d["cluster"]
        if d["type"] == "unmatched":
            act = r.get("action")
            if act == "drop":
                drop.add(cid)
            elif act == "same_as":
                tgt = r.get("target")
                if tgt not in clusters or tgt == cid:
                    raise ReconcileError(f"{d['id']}: same_as target {tgt!r} is not another cluster")
                same_as[cid] = tgt
            elif act != "keep":
                raise ReconcileError(f"{d['id']}: action one of keep, drop, same_as")
            if act == "keep" and d.get("categories") and not r.get("upper_category"):
                raise ReconcileError(f"{d['id']}: its members disagree on the category {d['categories']}; "
                                     f"upper_category required")
        elif not r.get("upper_category"):
            raise ReconcileError(f"{d['id']}: upper_category required")
        if r.get("upper_category"):
            if r["upper_category"] not in cats_ok:
                raise ReconcileError(f"{d['id']}: upper_category one of {sorted(cats_ok)}")
            overrides.setdefault(cid, {})["upper_category"] = r["upper_category"]
        if r.get("concept"):
            overrides.setdefault(cid, {}).update(r["concept"])

    def final_target(cid: str) -> str:
        seen = [cid]
        while cid in same_as:
            cid = same_as[cid]
            if cid in seen:
                raise ReconcileError(f"same_as cycle: {' → '.join(seen + [cid])}")
            seen.append(cid)
        return cid

    dropped: set[str] = set(drop)
    for cid in list(same_as):
        tgt = final_target(cid)
        if tgt in drop:
            raise ReconcileError(f"{cid}: same_as leads to {tgt}, which is dropped; keep {tgt} or drop {cid}")
        clusters[tgt] += clusters[cid]
        clusters[cid] = []
        for k, v in overrides.pop(cid, {}).items():
            overrides.setdefault(tgt, {}).setdefault(k, v)
        dropped.add(cid)
    # concept ids: the first member's id, unique
    final, id_of_ref, used = [], {}, set()
    for c in rec["clusters"]:
        cid = c["id"]
        if cid in dropped:
            continue
        mem = clusters[cid]
        mem.sort(key=lambda r: order.index(r.split(":", 1)[0]))
        concept = _merge([by_ref[m] for m in mem])
        concept.update({k: v for k, v in overrides.get(cid, {}).items() if k != "id"})
        new_id, k = concept["id"], 2
        while new_id in used:
            new_id, k = f"{concept['id']}-{k}", k + 1
        concept["id"] = new_id
        used.add(new_id)
        for m in mem:
            id_of_ref[m] = new_id
        final.append(concept)
    # broader: every member's links, remapped through its own extractor, united
    for c in final:
        refs = [m for m, v in id_of_ref.items() if v == c["id"]]
        broader = set()
        for m in refs:
            e = m.split(":", 1)[0]
            for b in by_ref[m].get("broader", []):
                if f"{e}:{b}" in id_of_ref:
                    broader.add(id_of_ref[f"{e}:{b}"])
                elif ":" in b:
                    broader.add(b)
        c.pop("broader", None)
        if broader - {c["id"]}:
            c["broader"] = sorted(broader - {c["id"]})
    rels, seen = [], set()
    for e in order:
        for r in exs[e].get("relations", []):
            f, t = id_of_ref.get(f"{e}:{r['from']}"), id_of_ref.get(f"{e}:{r['to']}")
            if not f or not t or f == t or (f, r["type"], t) in seen:
                continue
            seen.add((f, r["type"], t))
            rels.append({**r, "from": f, "to": t})
    first = exs[order[0]]
    doc = {k: v for k, v in first.items() if k not in ("concepts", "relations", "extractor", "absences", "caveats",
                                                         "variation", "proposed_values", "proposed_shared")}
    doc["concepts"] = final
    doc["relations"] = rels
    absences = [a for e in order for a in exs[e].get("absences", [])]
    if absences:
        doc["absences"] = absences
    var, vseen = [], set()
    for e in order:
        for v in exs[e].get("variation", []):
            if (v["dimension"], v["value"]) not in vseen:
                vseen.add((v["dimension"], v["value"]))
                var.append(v)
    if var:
        doc["variation"] = var
    pv, pseen = [], set()
    for e in order:
        for v in exs[e].get("proposed_values", []):
            if (v.get("dimension"), v.get("id")) not in pseen:
                pseen.add((v.get("dimension"), v.get("id")))
                pv.append(v)
    if pv:
        doc["proposed_values"] = pv
    doc["caveats"] = [c for e in order for c in exs[e].get("caveats", [])]
    doc["extractors"] = [exs[e].get("extractor", {"id": e}) for e in order]
    doc["reconciled"] = {"run": run.id, "model": res_doc.get("model"), "agreement": rec["agreement_mean"]}
    save_json(out, doc)
    return {"concepts": len(final), "relations": len(rels), "dropped": len(dropped), "disputes": len(rec["disputes"])}
