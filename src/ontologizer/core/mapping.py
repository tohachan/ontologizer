"""Stage 4: map reconciled project concepts to the shared levels (docs/process.md §4).

- The decision port proposes candidate targets: a `choice` over the shared
  concepts plus `none`; options above the `map_candidate` threshold (at most
  three) are candidates. With decisions off, the candidates are the shared
  concepts with the most similar label and definition.
- The decision port gives a second opinion on the gUFO category (stage
  `category`); a category it does not confirm goes to the review list.
- An LLM decides each mapping (target and kind, or none) in
  `map/<p>.answers.json`; a `partial` one must say what overlaps and what differs,
  and stage 6 rechecks every partial; `--apply` writes `maps_to` into `application/<p>.json`.
"""

from __future__ import annotations

import json

from ontologizer.core.levels import MAPPING_KINDS, Preset
from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import application_file, shared_concepts
from ontologizer.core.similarity import TfIdf
from ontologizer.ports.decisions import Question

MAX_CANDIDATES = 3
CATEGORY_HINTS = {
    "Kind": "a rigid type of independent things with their own identity (a record, an agent, a repository)",
    "CollectionKind": "a rigid type of collections of members",
    "SubKind": "a rigid specialization of another kind",
    "Role": "a type something instantiates only while related to others (reviewer, worker, owner)",
    "Phase": "a type something instantiates only during a stage of its life (running, idle)",
    "Category": "a rigid type grouping things of several kinds by a shared property",
    "Mixin": "a semi-rigid type grouping things of several kinds",
    "RoleMixin": "a role played by things of several kinds",
    "PhaseMixin": "a phase of things of several kinds",
    "Relator": "a thing that exists only by connecting others (assignment, lease, claim, membership, contract)",
    "Event": "something that happens or runs in time (a dispatch, a merge, a run)",
    "Situation": "a configuration of things at a time (a state of affairs)",
    "Quality": "a measurable property of one thing (a size, a priority value)",
    "Mode": "an intrinsic, non-measurable property that depends on one thing (a skill, an intention)",
    "Abstract": "an abstract value: an enumeration, a configuration value, a schema, a policy text",
}


class MappingError(ValueError):
    pass


def _concept(c: dict) -> dict:
    out = {"label": c["label"], "alt_labels": c.get("alt_labels", []), "definition": c["definition"],
           "category": c.get("upper_category")}
    if c.get("broader"):   # a SubKind or a Phase is judged wrong without the concept it specializes
        out["broader"] = c["broader"]
    return out


def analyse(run: Run, project: str, engine=None) -> dict:
    f = application_file(run.cfg, project)
    if not f.is_file():
        raise MappingError(f"{project}: {f} missing; reconcile it first (stage 3)")
    doc = json.loads(f.read_text())
    preset = Preset.load(run.cfg.preset_path)
    shared = shared_concepts(run.cfg, engine)
    by_ref = {s["ref"]: s for s in shared}
    dmap, dcat = run.decider("map"), run.decider("category")
    thr = run.threshold("map_candidate")
    cat_thr = run.threshold("category_agree")
    options = {s["ref"]: f"{s['label']} ({s['level']}): {s['definition']}" for s in shared}
    options["none"] = "no shared concept corresponds: the concept is specific to this project"
    tfidf = TfIdf({**{s["ref"]: s for s in shared}, **{f"@{c['id']}": c for c in doc["concepts"]}})
    cats = {k: CATEGORY_HINTS.get(k, k) for k in preset.categories}
    target_q = ("Which shared concept does {what} correspond to: the same thing, a kind of it, a role of it, a "
                "realization of it, or a partial overlap? Choose none when nothing corresponds.")
    cat_q = "Which upper-ontology category (gUFO) fits {what} best?"
    batch = max(1, int((run.decisions_conf or {}).get("map_batch", 20)))
    tgt_ans: dict[str, object] = {}
    cat_ans: dict[str, object] = {}
    cs = doc["concepts"]
    for i in range(0, len(cs), batch):
        chunk = cs[i:i + batch]
        if batch == 1:   # one concept per request (the state shape of the first runs, kept for their caches)
            state = {"task": f"A concept of the software project {project!r}.", "concept": _concept(chunk[0])}
            ids = [("target", "category", "this project concept", "this concept")]
        else:
            state = {"task": f"Concepts of the software project {project!r}.",
                     "concepts": [{"n": n, **_concept(c)} for n, c in enumerate(chunk)]}
            ids = [(f"t{n}", f"k{n}", f"project concept n={n}", f"concept n={n}") for n in range(len(chunk))]
        if shared and dmap.on:
            a = dmap.ask(state, [Question(t, "choice", options=options, instructions=target_q.format(what=w))
                                 for t, _, w, _ in ids])
            for c, (t, _, _, _) in zip(chunk, ids):
                tgt_ans[c["id"]] = a.get(t)
        if dcat.on:
            a = dcat.ask(state, [Question(k, "choice", options=cats, instructions=cat_q.format(what=w))
                                 for _, k, _, w in ids])
            for c, (_, k, _, _) in zip(chunk, ids):
                cat_ans[c["id"]] = a.get(k)
    items = []
    for c in doc["concepts"]:
        it = {"id": c["id"], "label": c["label"], "category": c.get("upper_category")}
        # candidates
        if shared and dmap.on:
            a = tgt_ans.get(c["id"])
            if a is not None and a.ok:
                probs = a.probabilities or {a.value: a.confidence if a.confidence is not None else 1.0}
                cands = sorted(((p, t) for t, p in probs.items() if t != "none" and t in by_ref and p >= thr), reverse=True)
                if a.value != "none" and a.value in by_ref and a.value not in {t for _, t in cands}:
                    cands.insert(0, (probs.get(a.value, 1.0), a.value))
                it["decision"] = {"choice": a.value, "p": round(float(probs.get(a.value, a.confidence or 0)), 3),
                                  "mode": dmap.mode}
                it["candidates"] = [{"target": t, "p": round(float(p), 3)} for p, t in cands[:MAX_CANDIDATES]]
        elif shared:
            scored = sorted(((tfidf.sim(f"@{c['id']}", s["ref"]), s["ref"]) for s in shared), reverse=True)
            it["candidates"] = [{"target": t, "score": round(sc, 3)} for sc, t in scored[:MAX_CANDIDATES] if sc > 0.05]
        else:
            it["candidates"] = []
        # category second opinion
        if dcat.on:
            a = cat_ans.get(c["id"])
            if a is not None and a.ok:
                p_ext = (a.probabilities or {}).get(c.get("upper_category"))
                agree = a.value == c.get("upper_category") or (p_ext is not None and p_ext >= cat_thr)
                it["category_opinion"] = {"value": a.value, "p_extraction": None if p_ext is None else round(p_ext, 3),
                                          "agree": agree, "mode": dcat.mode}
        items.append(it)
    dmap.done()
    dcat.done()
    compared = [i for i in items if "category_opinion" in i]
    out = {"project": project, "run": run.id, "at": now(), "modes": {"map": dmap.mode, "category": dcat.mode},
           "shared_concepts": len(shared), "concepts": items,
           "category_disagreement": {"compared": len(compared),
                                     "disagree": sum(1 for i in compared if not i["category_opinion"]["agree"]),
                                     "ids": [i["id"] for i in compared if not i["category_opinion"]["agree"]]},
           "answers_file": str(run.path("map", f"{project}.answers.json")), "how_to_answer": ANSWER_HELP}
    if run.overlay:   # stage 9: the shared levels are a release in the store, not files beside the answerer
        out["shared_graphs"] = {lv: g for lv, g in run.cfg.shared.items() if lv in ("domain", "task")}
        out["shared"] = [{"ref": x["ref"], **_concept(x)} for x in shared]
    save_json(run.path("map", f"{project}.json"), out)
    return out


ANSWER_HELP = (
    "Write <project>.answers.json as {\"model\": \"<model id>\", \"mappings\": {\"<concept id>\": "
    "[{\"target\": \"domain:<id>\" | \"task:<id>\", \"kind\": \"equivalence|specialization|roleOf|realization|partial\", "
    "\"confidence\": \"high|medium|low\", \"reason\": \"...\"}] or [] for none}, "
    "\"none\": {\"<concept id>\": \"why it has no target\"}, \"categories\": {\"<concept id>\": {\"upper_category\": \"...\", "
    "\"reason\": \"...\"}}}. Every concept needs an entry in mappings. `categories` is required for every id in "
    "category_disagreement.ids (keep or change the category, with the reason). The candidates are suggestions: "
    "any shared concept may be chosen. `partial` is the last resort, not the default: use it only when the concepts "
    "overlap without one being the same as, a kind of, a role of or a realization of the other, and then also give "
    "\"overlap\": \"what both cover\" and \"differs\": \"what one has that the other does not\". When you cannot "
    "name both, the answer is [] (none). Every partial mapping is rechecked by another model in stage 6.")


def apply(run: Run, project: str, engine=None) -> dict:
    m = load_json(run.path("map", f"{project}.json"))
    if m is None:
        raise MappingError(f"{project}: run `ontologizer map {project}` first")
    ans = load_json(run.path("map", f"{project}.answers.json"), {}) or {}
    maps, cats = ans.get("mappings") or {}, ans.get("categories") or {}
    f = application_file(run.cfg, project)
    doc = json.loads(f.read_text())
    preset = Preset.load(run.cfg.preset_path)
    allowed = {}
    for s, t, k in preset.allowed_mappings():
        allowed.setdefault((s, t), set()).add(k)
    known = {s["ref"] for s in shared_concepts(run.cfg, engine)}
    errs = []
    for c in doc["concepts"]:
        if c["id"] not in maps:
            errs.append(f"{c['id']}: no mapping decision")
            continue
        for x in maps[c["id"]]:
            t, k = x.get("target"), x.get("kind")
            if t not in known:
                errs.append(f"{c['id']}: unknown target {t!r}")
            elif k not in MAPPING_KINDS or k not in allowed.get((c["level"], t.split(":")[0]), set()):
                errs.append(f"{c['id']}: kind {k!r} not allowed from {c['level']} to {t.split(':')[0]}")
            if not x.get("reason"):
                errs.append(f"{c['id']}: mapping to {t} without a reason")
            if k == "partial" and not (str(x.get("overlap") or "").strip() and str(x.get("differs") or "").strip()):
                errs.append(f"{c['id']}: partial mapping to {t} needs `overlap` and `differs`")
    for cid in m["category_disagreement"]["ids"]:
        if not (cats.get(cid) or {}).get("reason"):
            errs.append(f"{cid}: disputed category without a decision and reason in `categories`")
    for cid, x in cats.items():
        if x.get("upper_category") and x["upper_category"] not in preset.categories:
            errs.append(f"{cid}: unknown category {x['upper_category']!r}")
    need = run.role_model("map")
    if need and ans.get("model") and ans["model"] != need:
        errs.append(f"answered by {ans['model']}; process.roles.map requires {need}")
    if not ans.get("model"):
        errs.append("`model` required in the answers file (stage 6 rechecks with a different model)")
    if errs:
        raise MappingError(f"{project}: " + "; ".join(errs[:15]) + (f" (+{len(errs) - 15})" if len(errs) > 15 else ""))
    changed = 0
    for c in doc["concepts"]:
        c["maps_to"] = [{"target": x["target"], "kind": x["kind"], "confidence": x.get("confidence", "medium"),
                         "note": x["reason"] + (f" Overlap: {x['overlap']} Differs: {x['differs']}"
                                                if x["kind"] == "partial" else "")} for x in maps[c["id"]]]
        if c["id"] in cats and cats[c["id"]].get("upper_category") and cats[c["id"]]["upper_category"] != c["upper_category"]:
            c["note"] = (c.get("note", "") + f" Category changed in stage 4 from {c['upper_category']}: "
                         f"{cats[c['id']]['reason']}").strip()
            c["upper_category"] = cats[c["id"]]["upper_category"]
            changed += 1
    doc.setdefault("reconciled", {})["mapped"] = {"run": run.id, "model": ans.get("model")}
    save_json(f, doc)
    # Jev–LLM disagreement on the target (when the map stage ran with Jev)
    dis = comp = 0
    for it in m["concepts"]:
        d = it.get("decision")
        if d and d.get("mode") == "jev":
            comp += 1
            chosen = {x["target"] for x in maps[it["id"]]} or {"none"}
            dis += d["choice"] not in chosen
    stats = {"mapped": sum(1 for c in doc["concepts"] if c["maps_to"]),
             "none": sum(1 for c in doc["concepts"] if not c["maps_to"]), "categories_changed": changed,
             "jev_llm_target": {"compared": comp, "disagree": dis}}
    out = run.path("map", f"{project}.json")
    m["applied"] = {"at": now(), **stats}
    save_json(out, m)
    return stats
