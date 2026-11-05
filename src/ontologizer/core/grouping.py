"""Stage 5 input: group unmapped concepts across projects by meaning (docs/process.md §5).

Code proposes cross-project candidate pairs (TF-IDF over label, alt labels and
definition; top K per concept). The decision port judges each pair (`noul`:
the same concept?); with decisions off, a pair is the same when a label is
equal. Code builds groups as connected components of accepted pairs and
counts the frame's architectural families behind each group. A strong model
then writes the decision file (`consolidation/<run>.yaml`); `consolidate`
refuses a shared concept without support from two families or without a reason.

The overlay target named by the frame is never read.
"""

from __future__ import annotations

from ontologizer.core.frame import Frame
from ontologizer.core.levels import Preset
from ontologizer.core.run import Run, now, save_json
from ontologizer.core.shared import application_docs
from ontologizer.core.similarity import TfIdf, same_label
from ontologizer.ports.decisions import Question

TOP_K = 5
MIN_SIM = 0.12
PAIR_BATCH = 20


def unmapped(run: Run, frame: Frame) -> dict[str, dict]:
    docs = application_docs(run.cfg, [p for p in frame.projects if p not in frame.overlay])
    return {f"{p}:{c['id']}": {**c, "project": p} for p, d in docs.items() for c in d["concepts"]
            if not c.get("maps_to")}


def build(run: Run, frame: Frame) -> dict:
    con = unmapped(run, frame)
    keys = sorted(con)
    tf = TfIdf(con)
    pairs: dict[tuple[str, str], float] = {}
    for a in keys:
        scored = sorted(((tf.sim(a, b), b) for b in keys if con[b]["project"] != con[a]["project"]), reverse=True)
        for s, b in scored[:TOP_K]:
            if s >= MIN_SIM or same_label(con[a], con[b]):
                pairs[tuple(sorted((a, b)))] = s
    plist = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    dec = run.decider("group")
    thr = run.threshold("group_same_concept")
    judged = []
    if dec.on:
        for i in range(0, len(plist), PAIR_BATCH):
            chunk = plist[i:i + PAIR_BATCH]
            state = {"task": "Concepts from different software projects of one corpus. Decide for each pair whether "
                             "both denote the same concept of the domain, even under different names.",
                     "pairs": [{"n": n, "first": _brief(con[a]), "second": _brief(con[b])}
                               for n, ((a, b), _) in enumerate(chunk)]}
            qs = [Question(f"p{n}", "noul", instructions=f"Pair n={n}: are 'first' and 'second' the same concept "
                                                         f"of the domain (not merely related)?")
                  for n in range(len(chunk))]
            ans = dec.ask(state, qs)
            for n, ((a, b), s) in enumerate(chunk):
                x = ans.get(f"p{n}")
                if x is not None and x.ok:
                    judged.append({"a": a, "b": b, "sim": round(s, 3), "p": round(float(x.value), 3)})
    else:
        judged = [{"a": a, "b": b, "sim": round(s, 3), "p": 1.0 if same_label(con[a], con[b]) else 0.0}
                  for (a, b), s in plist]
    dec.done()
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for j in judged:
        if j["p"] >= thr:
            parent[find(j["b"])] = find(j["a"])
    comps: dict[str, list[str]] = {}
    for k in keys:
        comps.setdefault(find(k), []).append(k)
    need = Preset.load(run.cfg.preset_path).min_families
    groups = []
    for n, mem in enumerate(sorted((sorted(v) for v in comps.values() if len(v) > 1), key=lambda v: (-len(v), v))):
        projects = sorted({con[m]["project"] for m in mem})
        fams = sorted({frame.family_of(p) or f"?{p}" for p in projects})
        groups.append({"id": f"g{n + 1}", "members": mem, "labels": sorted({con[m]["label"] for m in mem}),
                       "projects": projects, "families": fams, "eligible": len(fams) >= need})
    doc = {"run": run.id, "at": now(), "mode": dec.mode, "threshold": thr, "unmapped": len(keys),
           "pairs_judged": len(judged), "groups": groups,
           "singletons": sorted(k for v in comps.values() if len(v) == 1 for k in v),
           "concepts": {k: _brief(con[k]) for k in keys}, "judged": judged,
           "families": {f: v["projects"] for f, v in frame.families.items()}, "overlay_excluded": frame.overlay}
    save_json(run.path("consolidate", "groups.json"), doc)
    return doc


def _brief(c: dict) -> dict:
    return {"label": c["label"], "alt_labels": c.get("alt_labels", []), "definition": c["definition"],
            "category": c.get("upper_category"), "project": c.get("project")}
