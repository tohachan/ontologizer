"""ontologizer CLI.

  ontologizer status                         graphs, sizes, policies
  ontologizer bootstrap                      import pinned upper/kit graphs and generated shapes
  ontologizer ingest FILE.json [--dry-run]   extraction JSON → validate → SHACL → write route
  ontologizer validate FILE.json             extraction schema + SHACL, no write
  ontologizer export-graph PATH [--turtle]   one graph to stdout
  ontologizer import-graph PATH FILE.ttl     replace one research graph from Turtle (via route)
  ontologizer release PATH… VERSION          release graphs as one version (version-distinct term IRIs)
  ontologizer export-md                      markdown export per config
  ontologizer query FILE.rq|SPARQL           SELECT over the store (union of graphs via GRAPH ?g)
  ontologizer consolidation-report [FILE…]   consolidation input from extractions (default paths.application)
  ontologizer consolidate DECISIONS.yaml     apply a consolidation decision file to shared-level and application files
  ontologizer visualizer [--port 8090]       read-only web view of the ontology (docs/visualizer.md)

Analysis process (docs/process.md), each with --run ID (default `process.run`):
  ontologizer frame init|check               stage 0: frame.md from the template / validate it
  ontologizer gate approve GATE --by NAME    record the owner's approval of a file (frame) at its hash
  ontologizer inventory [PROJECT…]           stage 1: candidates per project (inventory adapter), triage
  ontologizer brief extract PROJECT --extractor ID [--parts N]   stage 2: extraction brief(s)
  ontologizer reconcile PROJECT [--apply]    stage 3: pairs, agreement, disputes / build application/<p>.json
  ontologizer map PROJECT [--apply]          stage 4: candidate targets, category second opinion / write maps_to
  ontologizer group                          stage 5: unmapped concepts grouped by meaning, family support
  ontologizer verify PROJECT [--apply]       stage 6: every claim against its source; rechecks
  ontologizer stability | report [--compare A B]   stage 7
  ontologizer release-compare A B [--run-a ID]    two releases by meaning: kept, added, dropped, with reasons
  ontologizer review build|apply --by MODEL [--effort E]   stage 8: agent review; gold sample NAME | gold calibrate FILE
  ontologizer overlay [--previous F --compare C]   stage 9: the target's verdict against a release

The config is found by walking up from the working directory (see core/config.py);
owner files resolve against that config's `paths:`, one directory per level:
`ontology/domain/`, `ontology/task/`, `ontology/variation/`, `ontology/application/` by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rdflib import RDF, Graph

from ontologizer.adapters.engine.open_ontologies import OpenOntologies
from ontologizer.adapters.exporter.markdown import MarkdownExporter
from ontologizer.core.config import KIT, Config
from ontologizer.core.extraction import ExtractionError, to_graph
from ontologizer.core.levels import Preset
from ontologizer.core.route import WriteRejected, WriteRoute


def _route(args) -> WriteRoute:
    cfg = Config.load(args.config)
    return WriteRoute(cfg, OpenOntologies(cfg.data.get("store", {}).get("url")))


def cmd_status(args) -> int:
    r = _route(args)
    side = f"project {r.cfg.project} (hub {r.cfg.kit_root})" if r.cfg.project else f"hub {r.cfg.root}"
    print(f"config: {r.cfg.root / 'ontologizer.yaml'} — {side}")
    for iri in r.engine.graph_names():
        try:
            path = r.cfg.graph_path(iri)
            pol = r.cfg.policy(path)
            mode, owner = pol.mode, pol.owner or "hub"
        except (ValueError, KeyError):
            path, mode, owner = iri, "-", "-"
        print(f"{path:32} {mode:12} {owner:13} {r.engine.graph_size(iri):6}")
    return 0


def cmd_bootstrap(args) -> int:
    r = _route(args)
    for path in ("upper/gufo", "kit/core"):
        res = r.import_pinned(path) if path == "upper/gufo" else None
        if path == "kit/core":
            pol = r.cfg.policy(path)
            g = Graph().parse((pol.base or r.cfg.root) / pol.source)
            g.parse(data=r.preset.levels_turtle(), format="turtle")
            res = r.import_pinned(path, g)
        print(f"{path}: {'imported' if res.changed else 'unchanged'} ({res.triples} triples)")
    # Shapes are not stored: the route generates them per graph role from the preset.
    out = r.cfg.kit_root / "shapes"
    out.mkdir(exist_ok=True)
    for role in ("domain", "task", "variation", "project"):
        (out / f"{r.preset.name}.{role}.ttl").write_text(r.preset.shapes_turtle(role))
    print(f"shapes: generated {out}/{r.preset.name}.*.ttl (reference copies)")
    return 0


def _load_doc(r: WriteRoute, file: str) -> tuple[dict, Graph]:
    from ontologizer.core.extraction import validate
    doc = json.loads(Path(file).read_text())
    preset = Preset.load(r.cfg.preset_path)
    cap = (r.cfg.data.get("process") or {}).get("max_concepts")
    errs = validate(doc, preset, max_concepts=cap)
    if errs:
        raise ExtractionError(errs)
    return doc, to_graph(doc, r.cfg, preset)


def cmd_ingest(args) -> int:
    r = _route(args)
    try:
        doc, g = _load_doc(r, args.file)
        res = r.write(doc["graph"], g, op="replace", reason=f"ingest {Path(args.file).name}",
                      dry_run=args.dry_run)
    except ExtractionError as e:
        print("extraction schema errors:\n- " + "\n- ".join(e.errors), file=sys.stderr)
        return 2
    except WriteRejected as e:
        print(str(e), file=sys.stderr)
        return 3
    verb = "would change" if args.dry_run and res.changed else ("written" if res.changed else "unchanged")
    print(f"{res.graph}: {verb}; {res.triples} triples (+{res.added} −{res.removed}) hash {res.content_hash[:12]}")
    return 0


def cmd_validate(args) -> int:
    args.dry_run = True
    return cmd_ingest(args)


def cmd_export_graph(args) -> int:
    r = _route(args)
    g = r.read_graph(args.path)
    sys.stdout.write(g.serialize(format="turtle") if args.turtle else r.engine.export_graph(r.cfg.graph_iri(args.path)))
    return 0


def cmd_import_graph(args) -> int:
    r = _route(args)
    g = Graph().parse(args.file)
    if r.cfg.policy(args.path).role and len(g) and not any(str(o).startswith(KIT) for o in g.objects(None, RDF.type)):
        print(f"refused: {args.file} uses none of the kit's vocabulary, so in a `{r.cfg.policy(args.path).role}` graph "
              f"the kit would see nothing in it, and the write would replace all of {args.path}. To analyse it, make it "
              f"a source of the staged process (docs/process.md §10). To keep it as it is, import it into a graph with "
              f"no role.", file=sys.stderr)
        return 3
    try:
        res = r.write(args.path, g, op="replace", reason=f"import {args.file}")
    except WriteRejected as e:
        print(str(e), file=sys.stderr)
        return 3
    print(f"{res.graph}: {'written' if res.changed else 'unchanged'} ({res.triples} triples)")
    return 0


def cmd_release(args) -> int:
    r = _route(args)
    *args.paths, args.version = args.items          # release PATH … VERSION | release --migrate-iris VERSION
    if not args.migrate_iris and not args.paths:
        print("release needs PATH … VERSION", file=sys.stderr)
        return 2
    if (r.cfg.data.get("process") or {}) and not args.migrate_iris and not args.unreviewed and \
            any(r.cfg.policy(p).role in ("domain", "task") for p in args.paths):
        from ontologizer.core.review import require_review
        ok, why = require_review(r.cfg)
        if not ok:
            print(f"release refused: {why}; pass --unreviewed to release anyway (recorded as such)", file=sys.stderr)
            return 3
        from ontologizer.core.update_check import require_update_check
        ok, why = require_update_check(r.cfg, r.engine)
        if not ok:
            print(f"release refused: {why}; pass --unreviewed to release anyway (recorded as such)", file=sys.stderr)
            return 3
    if args.migrate_iris:
        for res in r.migrate_release_iris(args.version):
            print(f"{res.graph}: {'rewritten to version-distinct IRIs' if res.changed else 'already version-distinct'} "
                  f"({res.triples} triples)")
        return 0
    try:
        results = r.release(args.paths, args.version)
    except WriteRejected as e:
        print(str(e), file=sys.stderr)
        return 3
    if args.unreviewed:
        import datetime as dt
        from ontologizer.core.frame import record
        record(r.cfg, {"gate": "unreviewed-release", "graphs": [f"{p}@{args.version}" for p in args.paths],
                       "at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()})
    for res in results:
        print(f"released {res.graph} ({res.triples} triples, hash {res.content_hash[:12]})")
    return 0


def cmd_export_md(args) -> int:
    r = _route(args)
    conf = r.cfg.exporter("markdown")
    if conf is None:
        print("no markdown exporter in this config", file=sys.stderr)
        return 1
    snap = r.engine.snapshot(prefix=r.cfg.graph_iri(""))
    # Pages only for graphs this config owns; `context` graphs feed the views.
    res = MarkdownExporter(r.cfg).export(snap, r.cfg.root / conf["out"], conf["graphs"],
                                         context=conf.get("context", []))
    warnings = res.pop("warnings", [])
    print(f"markdown: {res}")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


def cmd_query(args) -> int:
    r = _route(args)
    q = Path(args.sparql).read_text() if Path(args.sparql).is_file() else args.sparql
    rows = r.engine.select(q)
    if not rows:
        print("(no rows)")
    for row in rows:
        print(" | ".join(f"{k}={v}" for k, v in row.items()))
    return 0


def cmd_consolidation_report(args) -> int:
    from ontologizer.core.consolidation_report import report
    cfg = Config.load(args.config)
    report(args.files or sorted(str(p) for p in cfg.path("application").glob("*.json")))
    return 0


def cmd_consolidate(args) -> int:
    from ontologizer.core.consolidation import apply
    cfg = Config.load(args.config)
    from ontologizer.core.shared import shared_file
    frame = _frame_or_none(cfg)
    confirmation = Preset.load(cfg.preset_path).min_families
    return apply(args.decisions, {lv: shared_file(cfg, lv) for lv in ("domain", "task")},
                 cfg.path("application"), frame=frame,
                 min_families=frame.admission_families(confirmation) if frame else confirmation)


def _frame_or_none(cfg: Config):
    """The frame when the owner runs the staged process (a `process:` section), else None."""
    if not cfg.data.get("process"):
        return None
    from ontologizer.core.frame import Frame
    return Frame.load(cfg)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["visualizer"]:
        from ontologizer.adapters.visualization.server import main as serve
        return serve(argv[1:])
    p = argparse.ArgumentParser(prog="ontologizer")
    p.add_argument("--config", default=None,
                   help="default: $ONTOLOGIZER_CONFIG, else onto/ontologizer.yaml or ontologizer.yaml at or above cwd")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("bootstrap").set_defaults(fn=cmd_bootstrap)
    s = sub.add_parser("ingest"); s.add_argument("file"); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("validate"); s.add_argument("file"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("export-graph"); s.add_argument("path"); s.add_argument("--turtle", action="store_true")
    s.set_defaults(fn=cmd_export_graph)
    s = sub.add_parser("import-graph"); s.add_argument("path"); s.add_argument("file")
    s.set_defaults(fn=cmd_import_graph)
    s = sub.add_parser("release", help="release PATH [PATH …] VERSION: one version of a set of graphs")
    s.add_argument("items", nargs="+", metavar="PATH|VERSION", help="graph paths, then the version")
    s.add_argument("--unreviewed", action="store_true", help="release a shared level without the stage-8 review gate")
    s.add_argument("--migrate-iris", action="store_true",
                   help="rewrite the existing releases of VERSION to version-distinct IRIs (release --migrate-iris VERSION)")
    s.set_defaults(fn=cmd_release)
    sub.add_parser("export-md").set_defaults(fn=cmd_export_md)
    s = sub.add_parser("query"); s.add_argument("sparql"); s.set_defaults(fn=cmd_query)
    s = sub.add_parser("consolidation-report"); s.add_argument("files", nargs="*")
    s.set_defaults(fn=cmd_consolidation_report)
    s = sub.add_parser("consolidate"); s.add_argument("decisions"); s.set_defaults(fn=cmd_consolidate)
    from ontologizer.process_cli import register
    register(sub)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
