"""Stage 9 verdict: the overlay target against a released shared level (docs/process.md §9).

From the target's application file after stages 2–4 and 6 of an overlay run: which shared concepts
it realizes (and how: the mapping kinds), which of its concepts have no shared counterpart, and
which shared concepts it lacks. With an earlier application file mapped onto an earlier release
and a release comparison between the two (`release-compare`), the verdict lists what changed:
each shared concept of the new release that the target now realizes or lacks, against what it
did before, through the correspondence of the releases.
"""

from __future__ import annotations

import json
from pathlib import Path

from ontologizer.core.release_compare import translate
from ontologizer.core.run import Run, now, save_json
from ontologizer.core.shared import application_file, shared_concepts


def _realized(doc: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in doc.get("concepts", []):
        for m in c.get("maps_to", []):
            if m.get("target"):
                out.setdefault(m["target"], []).append({"concept": c["id"], "label": c["label"], "kind": m["kind"]})
    return out


def _summary(doc: dict, shared_refs: list[str]) -> dict:
    real = _realized(doc)
    cs = doc.get("concepts", [])
    mapped = [c for c in cs if any(m.get("target") for m in c.get("maps_to", []))]
    covered = [r for r in shared_refs if r in real]
    return {"concepts": len(cs), "mapped": len(mapped), "mapped_share": round(len(mapped) / len(cs), 3) if cs else None,
            "shared": len(shared_refs), "realized": len(covered),
            "realized_share": round(len(covered) / len(shared_refs), 3) if shared_refs else None}


def verdict(run: Run, frame, engine=None, previous: dict | None = None, comparison: dict | None = None) -> dict:
    shared = shared_concepts(run.cfg, engine)
    by_ref = {s["ref"]: s for s in shared}
    refs = sorted(by_ref)
    targets = {}
    for t in frame.overlay:
        f = application_file(run.cfg, t)
        if not f.is_file():
            continue
        doc = json.loads(f.read_text())
        real = _realized(doc)
        tv = {"summary": _summary(doc, refs),
              "realized": {r: real[r] for r in refs if r in real},
              "unknown_targets": sorted(set(real) - set(by_ref)),
              "no_counterpart": [{"concept": c["id"], "label": c["label"], "category": c.get("upper_category")}
                                 for c in doc["concepts"] if not any(m.get("target") for m in c.get("maps_to", []))],
              "lacks": [r for r in refs if r not in real]}
        if previous is not None and comparison is not None:
            tv["changes"] = changes(previous, doc, refs, comparison)
        targets[t] = tv
    return {"run": run.id, "at": now(), "shared_graphs": {k: v for k, v in run.cfg.shared.items()},
            "shared": {r: {"label": by_ref[r]["label"], "level": by_ref[r]["level"]} for r in refs},
            "targets": targets}


def changes(prev: dict, cur: dict, refs: list[str], comparison: dict) -> dict:
    """Per shared concept of the new release: realized before (through the correspondence) and now."""
    tr = translate(comparison)
    old_real = _realized(prev)
    before: dict[str, list[str]] = {}
    dropped_targets = {}
    for old_ref, who in old_real.items():
        new = tr.get(old_ref)
        if new is None:
            dropped_targets[old_ref] = {"reason": "not in the earlier release", "by": [w["concept"] for w in who]}
            continue
        if not new:
            dropped_targets[old_ref] = {"reason": "dropped in the new release", "by": [w["concept"] for w in who]}
        for n in new:
            before.setdefault(n, []).append(old_ref)
    corresponded = {r for x in comparison["correspondence"] for r in x["b"] if x["a"]}
    now_real = _realized(cur)
    rows = {}
    for r in refs:
        was, is_ = r in before, r in now_real
        if r not in corresponded:
            status = "new concept, realized" if is_ else "new concept, lacking"
        elif was and is_:
            status = "still realized"
        elif was:
            status = "no longer realized"
        elif is_:
            status = "newly realized"
        else:
            status = "still lacking"
        rows[r] = {"status": status, "before": sorted(before.get(r, [])),
                   "now": sorted({w["concept"] for w in now_real.get(r, [])})}
    counts: dict[str, int] = {}
    for v in rows.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    return {"counts": counts, "shared": rows, "earlier_targets_dropped": dropped_targets,
            "before": _summary(prev, sorted({r for x in comparison["correspondence"] for r in x["a"]})),
            "after": _summary(cur, refs)}


def render(v: dict) -> str:
    lines = [f"# Overlay verdict: run {v['run']}", "", f"Shared levels: {v['shared_graphs']}. Generated {v['at']}.", ""]
    for t, tv in v["targets"].items():
        s = tv["summary"]
        lines += [f"## {t}", "",
                  f"{s['concepts']} concepts, {s['mapped']} mapped ({s['mapped_share']}); realizes {s['realized']} of "
                  f"{s['shared']} shared concepts ({s['realized_share']}).", "",
                  "### Realized", "", "| Shared concept | Target concepts (kind) |", "| --- | --- |"]
        for r, who in tv["realized"].items():
            lines.append(f"| {v['shared'][r]['label']} (`{r}`) | "
                         f"{', '.join(f'{w['label']} (`{w['concept']}`, {w['kind']})' for w in who)} |")
        lines += ["", "### Lacks", ""]
        lines += [f"- {v['shared'][r]['label']} (`{r}`)" for r in tv["lacks"]] or ["- nothing"]
        lines += ["", "### No shared counterpart", ""]
        lines += [f"- {c['label']} (`{c['concept']}`, {c['category']})" for c in tv["no_counterpart"]] or ["- none"]
        ch = tv.get("changes")
        if ch:
            b, a = ch["before"], ch["after"]
            lines += ["", "### Changes against the earlier verdict", "",
                      "| | Before | Now |", "| --- | --- | --- |",
                      f"| concepts | {b['concepts']} | {a['concepts']} |",
                      f"| mapped share | {b['mapped_share']} | {a['mapped_share']} |",
                      f"| shared concepts realized | {b['realized']}/{b['shared']} ({b['realized_share']}) | "
                      f"{a['realized']}/{a['shared']} ({a['realized_share']}) |", "",
                      "| Status | Count |", "| --- | --- |"]
            lines += [f"| {k} | {n} |" for k, n in sorted(ch["counts"].items())]
            lines += ["", "| Shared concept | Status | Before (earlier release) | Now (target concepts) |",
                      "| --- | --- | --- | --- |"]
            for r, row in ch["shared"].items():
                lines.append(f"| {v['shared'][r]['label']} (`{r}`) | {row['status']} | "
                             f"{', '.join(f'`{x}`' for x in row['before']) or '—'} | "
                             f"{', '.join(f'`{x}`' for x in row['now']) or '—'} |")
            if ch["earlier_targets_dropped"]:
                lines += ["", "Earlier mappings whose target has no counterpart now:", ""]
                lines += [f"- `{r}` ({x['reason']}), from {', '.join(f'`{c}`' for c in x['by'])}"
                          for r, x in ch["earlier_targets_dropped"].items()]
        lines.append("")
    return "\n".join(lines)


def write(run: Run, v: dict) -> Path:
    save_json(run.path("overlay.json"), v)
    run.path("overlay.md").write_text(render(v))
    return run.path("overlay.md")
