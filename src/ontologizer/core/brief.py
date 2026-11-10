"""Stage 2 briefs: one per project and extractor, for a Claude subagent or a
cross-harness session (e.g. Codex, docs/cross-harness.md).

The brief carries the frame and the inventory and no shared vocabulary, so
earlier results cannot anchor the extraction. A large project can be split
into parts by module: `--parts N` writes N briefs over balanced module groups,
each producing `extract/<p>/<extractor>.part-<k>.json`; `load_extraction`
merges the parts.
"""

from __future__ import annotations

import json
from pathlib import Path

from ontologizer.core.frame import Frame
from ontologizer.core.levels import RELATION_TYPES, Preset
from ontologizer.core.run import Run, load_json
from ontologizer.core.sources import source_info

KIT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_LIST_MAX = 400


def extractor(run: Run, ex_id: str) -> dict:
    for e in run.extractors:
        if e["id"] == ex_id:
            return e
    raise KeyError(f"extractor {ex_id!r} not in process.extractors ({[e['id'] for e in run.extractors]})")


def output_path(run: Run, project: str, ex_id: str, part: int | None = None) -> Path:
    name = f"{ex_id}.json" if part is None else f"{ex_id}.part-{part}.json"
    return run.path("extract", project, name)


def module_groups(cands: list[dict], parts: int) -> list[list[str]]:
    """Modules split into `parts` groups of balanced candidate counts (largest first)."""
    sizes: dict[str, int] = {}
    for c in cands:
        sizes[c.get("module") or "."] = sizes.get(c.get("module") or ".", 0) + 1
    groups: list[list[str]] = [[] for _ in range(parts)]
    load = [0] * parts
    for m, n in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])):
        i = load.index(min(load))
        groups[i].append(m)
        load[i] += n
    return [sorted(g) for g in groups if g]


def render(run: Run, frame: Frame, project: str, ex_id: str, part: int | None = None,
           modules: list[str] | None = None) -> str:
    ex = extractor(run, ex_id)
    inv = load_json(run.path("inventory", f"{project}.json"), {"status": "missing"})
    src = source_info(run.cfg, project)
    preset = Preset.load(run.cfg.preset_path)
    if inv.get("status") == "measured":
        from ontologizer.core.inventory import ACCOUNTED_KINDS
        everything = [c for c in inv["candidates"] if c.get("counted")]
        if modules is not None:
            everything = [c for c in everything if (c.get("module") or ".") in modules]
        cands = [c for c in everything if c.get("kind") not in ACCOUNTED_KINDS]
        others = [c for c in everything if c.get("kind") in ACCOUNTED_KINDS]
        shown = cands[:INVENTORY_LIST_MAX]
        rows = "\n".join(f"| `{c['id']}` | {c['name']} | {c['kind']} | `{c['path']}`"
                         f"{':' + c['lines'] if c.get('lines') else ''} | {c.get('module') or ''} |" for c in shown)
        more = f"\n\n{len(cands) - len(shown)} more candidates are in `{run.path('inventory', project + '.json')}` " \
               f"(`counted: true`)." if len(cands) > len(shown) else ""
        scope = f" Your part covers the modules: {', '.join(modules)}." if modules is not None else ""
        inventory_line = (f"Inventory: {len(cands)} candidates to cover, listed below, most central first. Coverage "
                          f"is measured against this list: cover what is architectural, cite the ids in "
                          f"`inventory`, and say in the report which you left out and why.{scope}")
        inventory_block = "| id | name | kind | where | module |\n| --- | --- | --- | --- | --- |\n" + rows + more
        if others:
            o_shown = others[:INVENTORY_LIST_MAX]
            o_rows = "\n".join(f"| `{c['id']}` | {c['name']} | {c['kind']} | `{c['path']}`"
                               f"{':' + c['lines'] if c.get('lines') else ''} |" for c in o_shown)
            o_more = f"\n\n{len(others) - len(o_shown)} more are in the inventory file." if len(others) > len(o_shown) else ""
            inventory_block += (
                f"\n\n**Also account for** these {len(others)} terms, which are not concepts by themselves. "
                f"Coverage is counted per kind against this list too. List each id in the `inventory` of what "
                f"carries it: a property in the relation (or the concept) that states it; a shape in the "
                f"`caveats` entry that states its rule; an individual in the `caveats` entry that reports it as "
                f"an example, or in the concept it is a worked instance of; a scheme or ontology header in the "
                f"caveat on the source's form. A caveat that lists ids takes the object form "
                f"`{{\"text\", \"evidence\", \"inventory\"}}`. Say in the report which you left out and why.\n\n"
                f"| id | name | kind | where |\n| --- | --- | --- | --- |\n" + o_rows + o_more)
    else:
        inventory_line = ("Inventory: none for this project (coverage is unmeasured). Find the concepts from the "
                          "documentation and the code structure; leave `inventory` empty.")
        inventory_block = ""
    variation_block = _variation_block(frame)
    domains = "; ".join(f"**{d['label']}**: {d['boundary']}" for d in frame.h.get("domains", []))
    cap = run.max_concepts
    cmd = f"uv run --project {KIT_ROOT} ontologizer validate {output_path(run, project, ex_id, part)}"
    template = "extract-brief-description.md" if frame.source_kind(project) == "description" else "extract-brief.md"
    return (KIT_ROOT / "templates" / template).read_text().format(
        project=project, extractor_id=ex_id, part_label="" if part is None else f", part {part}",
        run=run.id, frame_summary=f"Domains: {domains}", out_of_scope="; ".join(frame.h.get("out_of_scope") or [])
        or "nothing listed", never_read=_never_read(run, frame, src["dir"]),
        commit=src["commit"] or "<unknown: report it>", source_dir=src["dir"] or "<unknown: report it>",
        inventory_line=inventory_line, inventory_block=inventory_block, variation_block=variation_block,
        cap_line=f"Extract at most {cap} concepts (pilot cap)." if cap else "There is no cap on the number of concepts.",
        categories=", ".join(f"`{c}`" for c in preset.categories), relation_types=", ".join(RELATION_TYPES),
        output=output_path(run, project, ex_id, part), harness=ex.get("harness", "claude"),
        model=ex.get("model", ""), owner_dir=run.cfg.root, validate_cmd=cmd)


def _never_read(run: Run, frame: Frame, source_dir: str | None) -> str:
    if run.overlay:   # stage 9: the target itself is read; an earlier analysis of it must not anchor this one
        own = run.cfg.root
        inside = source_dir and own.resolve().is_relative_to(Path(source_dir).resolve())
        where = f"`{own.resolve().relative_to(Path(source_dir).resolve())}/`" if inside else f"`{own}`"
        return (f"This is an overlay run (stage 9). Never read the earlier ontology analysis of this project: "
                f"{where} in the checkout (its ontology files, exports and findings), so earlier results do not "
                f"anchor the extraction.")
    return f"Never read these projects: {', '.join(frame.overlay) or 'none'}."


def _variation_block(frame: Frame) -> str:
    dims = frame.dimensions
    if not dims:
        return ""
    rows = []
    for d in dims:
        vals = "; ".join(f"`{v['id']}`: {v['definition']}" for v in d["values"])
        rows.append(f"- **`{d['id']}`** ({d['label']}): {d['definition']} Values: {vals}")
    return ("**Variation.** The study compares projects on these dimensions. For each dimension, add one entry per "
            "value the project takes to `variation`: `{\"dimension\", \"value\", \"evidence\": [...], \"note\"}`, "
            "with evidence as for concepts. A project may take several values of one dimension; say which is "
            "primary in `note`. When no value fits, add `{\"dimension\", \"id\", \"label\", \"definition\"}` to "
            "`proposed_values` and use that id. When a dimension does not apply, say so in `caveats` instead of "
            "guessing.\n\n" + "\n".join(rows))


def write_briefs(run: Run, frame: Frame, project: str, ex_id: str, parts: int = 1) -> list[Path]:
    out = []
    if parts <= 1:
        p = run.path("briefs", f"extract-{project}-{ex_id}.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(run, frame, project, ex_id))
        return [p]
    inv = load_json(run.path("inventory", f"{project}.json"), {})
    cands = [c for c in inv.get("candidates", []) if c.get("counted")]
    for k, mods in enumerate(module_groups(cands, parts), start=1):
        p = run.path("briefs", f"extract-{project}-{ex_id}.part-{k}.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(run, frame, project, ex_id, part=k, modules=mods))
        out.append(p)
    return out


def load_extraction(run: Run, project: str, ex_id: str) -> dict | None:
    """The extraction of `project` by `ex_id`, its parts merged (ids kept unique by suffix)."""
    whole = output_path(run, project, ex_id)
    if whole.is_file():
        return json.loads(whole.read_text())
    parts = sorted(run.path("extract", project).glob(f"{ex_id}.part-*.json"))
    if not parts:
        return None
    docs = [json.loads(p.read_text()) for p in parts]
    merged = {k: v for k, v in docs[0].items() if k not in ("concepts", "relations", "absences", "caveats", "variation")}
    merged.update(concepts=[], relations=[], absences=[], caveats=[], variation=[])
    seen: set[str] = set()
    for d in docs:
        start = len(merged["concepts"])
        rename = {}
        for c in d.get("concepts", []):
            cid = c["id"]
            if cid in seen:
                k = 2
                while f"{cid}-{k}" in seen:
                    k += 1
                rename[cid] = f"{cid}-{k}"
                c = {**c, "id": rename[cid]}
            seen.add(c["id"])
            merged["concepts"].append(c)
        for c in merged["concepts"][start:]:
            if c.get("broader"):
                c["broader"] = [rename.get(b, b) for b in c["broader"]]
        for r in d.get("relations", []):
            merged["relations"].append({**r, "from": rename.get(r["from"], r["from"]), "to": rename.get(r["to"], r["to"])})
        merged["absences"] += d.get("absences", [])
        merged["caveats"] += d.get("caveats", [])
        merged["variation"] += [v for v in d.get("variation", []) if v not in merged["variation"]]
    return merged


def extractions(run: Run, project: str) -> dict[str, dict]:
    out = {}
    for e in run.extractors:
        d = load_extraction(run, project, e["id"])
        if d is not None:
            out[e["id"]] = d
    return out
