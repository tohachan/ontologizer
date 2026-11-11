"""Stage 1: the inventory of each project's concept candidates (docs/process.md §1).

The inventory adapter (config `inventory: {adapter: …}`) lists candidates
deterministically. With decisions on for stage `inventory`, each candidate is
triaged into one of TRIAGE; only `architectural` ones count for coverage.
Without a graph the project is `unmeasured`: coverage is unknown, not zero.
"""

from __future__ import annotations

from ontologizer.core import plugins
from ontologizer.core.run import Run, now, save_json
from ontologizer.ports.decisions import Question

TRIAGE = {
    "architectural": "a concept that carries the system's architecture: something it creates, stores, "
                     "schedules, names in its API/CLI, or whose lifecycle it manages",
    "implementation": "an implementation detail: helper, wrapper, adapter internals, utility type",
    "ui": "a user-interface component, view model or styling type",
    "test": "test code, fixture or mock",
    "config": "a configuration, options or settings structure",
}
STATE_BATCH = 20

# Candidates an extraction accounts for elsewhere than in a concept's `inventory`: a property in the relation or
# concept that carries it, a shape in the caveat that states its rule, an individual in the caveat that reports it
# as an example (or the concept it is a worked instance of), a scheme or header in the caveat on the source's form.
# They are counted per kind; the headline `inventory_coverage` stays over the other kinds, which concepts cover.
ACCOUNTED_KINDS = ("property", "shape", "individual", "scheme", "ontology")


def accounted_ids(app: dict) -> set[str]:
    """Every inventory id an extraction lists: in concepts, relations, constraints, and object-form caveats and
    absences."""
    out = {x for c in app.get("concepts", []) for x in c.get("inventory", [])}
    out |= {x for r in app.get("relations", []) for x in r.get("inventory", [])}
    out |= {x for k in app.get("constraints", []) for x in k.get("inventory", [])}
    for key in ("caveats", "absences"):
        out |= {x for c in app.get(key, []) if isinstance(c, dict) for x in c.get("inventory", [])}
    return out


def source(run: Run):
    conf = run.cfg.data.get("inventory")
    return plugins.load("inventory", conf, base=run.cfg.root) if conf else None


def triage_questions(cands: list) -> tuple[dict, list[Question]]:
    state = {"task": "Triage concept candidates found in a code base",
             "candidates": [{"n": i, "name": c.name, "kind": c.kind, "path": c.path, "module": c.module}
                            for i, c in enumerate(cands)]}
    qs = [Question(id=f"c{i}", type="choice", options=TRIAGE,
                   instructions=f"What is candidate n={i} ({c.name!r} in {c.path})?") for i, c in enumerate(cands)]
    return state, qs


def build(run: Run, project: str, src=None) -> dict:
    src = src if src is not None else source(run)
    out_path = run.path("inventory", f"{project}.json")
    if src is None or not src.available(project):
        why = "no inventory adapter configured" if src is None else "no source graph for this project"
        doc = {"project": project, "status": "unmeasured", "reason": why, "at": now()}
        save_json(out_path, doc)
        return doc
    cands = src.candidates(project)
    total = len(cands)
    cap = (run.cfg.data.get("inventory") or {}).get("max_candidates")
    if cap:
        cands = cands[: int(cap)]
    dec = run.decider("inventory")
    triage: dict[str, tuple[str, float | None]] = {}
    for i in range(0, len(cands), STATE_BATCH):
        chunk = cands[i:i + STATE_BATCH]
        state, qs = triage_questions(chunk)
        ans = dec.ask(state, qs)
        for j, c in enumerate(chunk):
            a = ans.get(f"c{j}")
            if a is not None and a.ok:
                triage[c.id] = (a.value, a.probabilities.get(a.value, a.confidence))
    dec.done()
    keep = run.threshold("triage_keep")
    items = []
    for c in cands:
        d = c.to_dict()
        if c.id in triage:
            d["triage"], p = triage[c.id]
            if p is not None:
                d["p"] = round(float(p), 3)
        if dec.on and "triage" not in d:
            d["triage"] = "untriaged"   # the answer failed: counted, so coverage is not inflated
        d["counted"] = (not dec.on) or d["triage"] == "untriaged" or \
            (d["triage"] == "architectural" and d.get("p", 1.0) >= keep)
        items.append(d)
    doc = {"project": project, "status": "measured", "at": now(), "version": src.version(project),
           "freshness": src.freshness(project), "triage": dec.mode, "total": total, "listed": len(items),
           "counted": sum(1 for d in items if d["counted"]),
           "untriaged": sum(1 for d in items if d.get("triage") == "untriaged"), "candidates": items}
    save_json(out_path, doc)
    return doc
