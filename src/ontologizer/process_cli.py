"""CLI commands of the analysis process (docs/process.md). Registered by ontologizer.cli.

Exit codes: 0 done; 2 a stage's input or gate is not satisfied; 4 decisions
wait for an LLM agent (llm mode); 5 a named adapter is not installed.
"""

from __future__ import annotations

import json
import shutil
import sys
from functools import wraps
from pathlib import Path

from ontologizer.core.config import Config
from ontologizer.core.plugins import PluginMissing
from ontologizer.core.run import PendingDecisions, Run, now

KIT_ROOT = Path(__file__).resolve().parents[2]


def _errors(fn):
    @wraps(fn)
    def run(args):
        from ontologizer.core.frame import FrameError
        from ontologizer.core.mapping import MappingError
        from ontologizer.core.reconcile import ReconcileError
        from ontologizer.core.review import ReviewError
        from ontologizer.core.verify import VerifyError
        try:
            return fn(args)
        except PendingDecisions as e:
            print(str(e), file=sys.stderr)
            return 4
        except PluginMissing as e:
            print(str(e), file=sys.stderr)
            return 5
        except FrameError as e:
            print("frame:\n- " + "\n- ".join(e.errors), file=sys.stderr)
            return 2
        except (ReconcileError, MappingError, VerifyError, ReviewError) as e:
            print(str(e), file=sys.stderr)
            return 2
    return run


def _cfg(args) -> Config:
    return Config.load(args.config)


def _run(args) -> Run:
    return Run(_cfg(args), getattr(args, "run", None))


def _frame(cfg: Config, approved: bool = True):
    from ontologizer.core.frame import Frame, require_frame
    return require_frame(cfg) if approved else Frame.load(cfg)


def _scope(run: Run, fr, projects: list[str]) -> str | None:
    """Why the projects may not be analysed in this run, or None. A run of stages 1–8 never reads
    the overlay target; an overlay run (stage 9, `process.overlay`) reads nothing else."""
    if run.overlay:
        bad = sorted(set(projects) - set(fr.overlay))
        return (f"refused: {bad} not the overlay target of the frame ({fr.overlay}); an overlay run "
                f"(stage 9) analyses only the target") if bad else None
    bad = sorted(set(projects) & set(fr.overlay))
    return f"refused: {bad} is the overlay target (frame); it is not analysed in stages 1–8" if bad else None


def _not_overlay(run: Run, stage: str) -> str | None:
    return (f"refused: {stage} in an overlay run; stage 9 consolidates nothing from the target "
            f"(docs/process.md §9)") if run.overlay else None


def _engine(cfg: Config):
    from ontologizer.adapters.engine.open_ontologies import OpenOntologies
    try:
        return OpenOntologies(cfg.data.get("store", {}).get("url"))
    except Exception:   # no token / no server: queries are skipped
        return None


# ---- stage 0 ---------------------------------------------------------------------
@_errors
def cmd_frame(args) -> int:
    from ontologizer.core.frame import Frame, check, frame_path
    cfg = _cfg(args)
    p = frame_path(cfg)
    if args.action == "variation":
        from ontologizer.core.frame import sync_variation
        out = sync_variation(cfg, Frame.load(cfg))
        print(f"variation: {len(Frame.load(cfg).dimensions)} frame dimensions written to {out}; ingest it before the projects")
        return 0
    if args.action == "init":
        if p.exists():
            print(f"{p} exists; not overwritten", file=sys.stderr)
            return 2
        shutil.copyfile(KIT_ROOT / "templates" / "frame.md", p)
        print(f"frame: {p} from the template; the research agent fills it (skill frame-study)")
        return 0
    presets = sorted(x.stem for x in (cfg.kit_root / "presets").glob("*.yaml"))
    overlay = (cfg.data.get("process") or {}).get("overlay")   # an overlay target reads the frame of a study
    errs = check(p.read_text(), presets, project=None if overlay else cfg.project) if p.is_file() else [f"no frame at {p}"]
    if errs:
        print("frame:\n- " + "\n- ".join(errs), file=sys.stderr)
        return 2
    from ontologizer.core.frame import gate_status
    fr = Frame.load(cfg)
    ok, why = gate_status(cfg, "frame", p)
    print(f"frame: valid; {len(fr.families)} families, {len(fr.projects)} projects, "
          f"{len(fr.questions)} competency questions, {len(fr.dimensions)} variation dimensions, "
          f"{len(fr.targets)} targets; {why}")
    return 0


@_errors
def cmd_gate(args) -> int:
    from ontologizer.core.frame import approve, check, frame_path
    cfg = _cfg(args)
    if args.gate != "frame":
        print("only the frame gate is approved here; the review gate is recorded by `review apply`", file=sys.stderr)
        return 2
    p = frame_path(cfg)
    errs = check(p.read_text()) if p.is_file() else [f"no frame at {p}"]
    if errs:
        print("frame not valid; not approved:\n- " + "\n- ".join(errs), file=sys.stderr)
        return 2
    e = approve(cfg, "frame", p, by=args.by, at=now(), note=args.note)
    print(f"gate frame: approved by {e['by']} at {e['at']} (sha256 {e['sha256'][:12]})")
    return 0


# ---- stage 1 ---------------------------------------------------------------------
@_errors
def cmd_inventory(args) -> int:
    from ontologizer.core.inventory import build, source
    run = _run(args)
    fr = _frame(run.cfg)
    projects = args.projects or (list(fr.overlay) if run.overlay else [p for p in fr.projects if p not in fr.overlay])
    why = _scope(run, fr, projects)
    if why:
        print(why, file=sys.stderr)
        return 2
    src = source(run)
    run.update_meta(stages={"inventory": run.mode("inventory")})
    for p in projects:
        if src is not None and args.rebuild_stale and src.available(p):
            fresh = src.freshness(p)
            if fresh.get("stale") and hasattr(src, "rebuild"):
                print(f"{p}: stale ({fresh['reason']}); rebuilding: {src.rebuild(p)}")
        d = build(run, p, src)
        if d["status"] == "measured":
            print(f"{p}: {d['listed']} candidates listed of {d['total']}, {d['counted']} counted "
                  f"(triage {d['triage']}); freshness {d['freshness'].get('reason')}")
        else:
            print(f"{p}: unmeasured ({d['reason']})")
    return 0


# ---- stage 2 ---------------------------------------------------------------------
@_errors
def cmd_brief(args) -> int:
    from ontologizer.core.brief import extractor, write_briefs
    run = _run(args)
    fr = _frame(run.cfg)
    why = _scope(run, fr, [args.project])
    if why:
        print(why, file=sys.stderr)
        return 2
    ex = extractor(run, args.extractor)
    paths = write_briefs(run, fr, args.project, args.extractor, parts=args.parts)
    run.update_meta(extractors=run.extractors)
    for p in paths:
        print(f"brief: {p}")
    if ex.get("harness") == "codex":
        print("cross-harness (docs/cross-harness.md), e.g.\n  codex exec -m " + str(ex.get("model", "<model>"))
              + f" -s workspace-write -C {run.cfg.root} --skip-git-repo-check \"Read the brief at {paths[0]} and do "
              f"exactly what it says. Your run_id is {ex['id']}-{args.project}-{run.id}.\"")
    return 0


# ---- stage 3 ---------------------------------------------------------------------
@_errors
def cmd_reconcile(args) -> int:
    from ontologizer.core import reconcile
    from ontologizer.core.shared import application_file
    run = _run(args)
    why = _scope(run, _frame(run.cfg), [args.project])
    if why:
        print(why, file=sys.stderr)
        return 2
    if args.apply:
        out = application_file(run.cfg, args.project)
        st = reconcile.apply(run, args.project, out)
        print(f"{args.project}: {out} — {st['concepts']} concepts, {st['relations']} relations, "
              f"{st['disputes']} disputes resolved, {st['dropped']} clusters dropped. Next: "
              f"`ontologizer ingest {out} --dry-run`, then `ingest`.")
        return 0
    d = reconcile.analyse(run, args.project)
    ag = ", ".join(f"{k} {v['agreement']} ({v['matched']} of {v['a']}+{v['b']})" for k, v in d["agreement"].items())
    print(f"{args.project}: agreement {ag}; {len(d['clusters'])} clusters, {len(d['disputes'])} disputes "
          f"(mode {d['mode']}). Resolutions go to {d['resolutions_file']}.")
    return 0


# ---- stage 4 ---------------------------------------------------------------------
@_errors
def cmd_map(args) -> int:
    from ontologizer.core import mapping
    run = _run(args)
    why = _scope(run, _frame(run.cfg), [args.project])
    if why:
        print(why, file=sys.stderr)
        return 2
    engine = _engine(run.cfg) if run.overlay else None   # an overlay maps onto a release in the store
    if args.apply:
        st = mapping.apply(run, args.project, engine)
        print(f"{args.project}: {st['mapped']} mapped, {st['none']} without a shared counterpart, "
              f"{st['categories_changed']} categories changed; Jev–LLM target disagreement "
              f"{st['jev_llm_target']['disagree']}/{st['jev_llm_target']['compared']}")
        return 0
    d = mapping.analyse(run, args.project, engine)
    cd = d["category_disagreement"]
    print(f"{args.project}: {len(d['concepts'])} concepts against {d['shared_concepts']} shared concepts "
          f"(modes {d['modes']}); category second opinion disagrees on {cd['disagree']}/{cd['compared']}. "
          f"Answers go to {d['answers_file']}.")
    return 0


# ---- stage 5 ---------------------------------------------------------------------
@_errors
def cmd_group(args) -> int:
    from ontologizer.core.grouping import build
    run = _run(args)
    why = _not_overlay(run, "stage 5 (group)")
    if why:
        print(why, file=sys.stderr)
        return 2
    fr = _frame(run.cfg)
    d = build(run, fr)
    elig = sum(1 for g in d["groups"] if g["eligible"])
    print(f"group: {d['unmapped']} unmapped concepts, {d['pairs_judged']} pairs judged (mode {d['mode']}), "
          f"{len(d['groups'])} groups, {elig} with ≥2 families → {run.path('consolidate', 'groups.json')}. "
          f"Decisions go to consolidation/{run.id}.yaml (skill analyse-corpus, stage 5).")
    return 0


# ---- stage 6 ---------------------------------------------------------------------
@_errors
def cmd_verify(args) -> int:
    from ontologizer.core import verify
    run = _run(args)
    why = _scope(run, _frame(run.cfg), [args.project])
    if why:
        print(why, file=sys.stderr)
        return 2
    if args.apply:
        st = verify.apply(run, args.project, keep_failed=args.keep_failed)
        print(f"{args.project}: {len(st['removed_claims'])} claims removed, {st['dropped_evidence']} evidence items "
              f"dropped, {st['rechecks_changed']} judgements changed by the recheck ({st['recheck']})")
        return 0
    d = verify.analyse(run, args.project)
    e = d["evidence"]
    print(f"{args.project}: {e['checked']} evidence items checked (mode {d['mode']}): {e['supported']} supported, "
          f"{e['unsupported']} unsupported, {e['missing']} missing, {e['unchecked']} unchecked; share {e['share']}; "
          f"{len(d['absences'])} absence claims; {d['rechecks']} items to recheck in "
          f"{run.path('verify', args.project + '.recheck.json')}")
    rc = verify.rechecker(run)
    if d["rechecks"] and rc:
        rc_path = run.path("verify", args.project + ".recheck.json")
        if rc.get("harness") == "codex":
            print("recheck (default, docs/cross-harness.md):\n  codex exec -m " + str(rc.get("model", "<model>"))
                  + f" -s workspace-write -C {run.cfg.root} --skip-git-repo-check \"Read {rc_path}; its "
                  f"`instructions` are your brief. Use model {rc.get('model')}, harness codex.\"")
        else:
            print(f"recheck (default): {rc.get('harness')} / {rc.get('model')} with {rc_path}")
        if rc.get("fallback"):
            fb = rc["fallback"]
            print(f"fallback when {rc.get('harness')} is unavailable: {fb.get('harness')} / {fb.get('model')} "
                  f"(record `fallback_reason` in the answers)")
    return 0


# ---- stage 7 ---------------------------------------------------------------------
@_errors
def cmd_stability(args) -> int:
    from ontologizer.core.report import stability
    run = _run(args)
    why = _not_overlay(run, "stage 7 (stability)")
    if why:
        print(why, file=sys.stderr)
        return 2
    d = stability(run, _frame(run.cfg))
    print(f"stability: {d['marks']} (stable share {d['stable_share']}, min families {d['min_families']})")
    return 0


@_errors
def cmd_report(args) -> int:
    from ontologizer.core import report
    cfg = _cfg(args)
    if args.compare:
        runs = (cfg.data.get("process") or {}).get("runs", "runs")
        da, db = (Path(x) if Path(x).exists() else cfg.root / runs / x for x in args.compare)
        a, b = report.load_report(da), report.load_report(db)
        c = report.compare(a, b)
        c["reconcile_pairs"] = report.compare_pairs(da if da.is_dir() else da.parent, db if db.is_dir() else db.parent)
        out = cfg.root / runs / f"compare-{c['a']}-{c['b']}.md"
        out.write_text(report.render_compare(c) + (report.render_pairs(c["reconcile_pairs"], c["a"], c["b"])
                                                    if c["reconcile_pairs"] else ""))
        (out.with_suffix(".json")).write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n")
        print(f"compare: {out}")
        return 0
    run = Run(cfg, args.run)
    d = report.report(run, _frame(cfg), engine=_engine(cfg))
    print(f"report: {run.path('report.md')}\n" + json.dumps(d["metrics"], ensure_ascii=False))
    return 0


@_errors
def cmd_release_compare(args) -> int:
    from ontologizer.core import release_compare as rc
    from ontologizer.core.frame import Frame
    run = _run(args)
    engine = _engine(run.cfg)
    if engine is None:
        print("release-compare reads the releases from the store; no store answers", file=sys.stderr)
        return 2
    run_a = Run(run.cfg, args.run_a) if args.run_a else None
    try:
        fr = Frame.load(run.cfg)
    except Exception:
        fr = None
    try:
        c = rc.compare(run.cfg, engine, args.a, args.b, run, run_a=run_a, frame=fr)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    out = rc.write(run, c)
    print(f"release-compare @{args.a} → @{args.b}: {c['counts']}; {c['pairs_judged']} pairs judged "
          f"(mode {c['mode']}), ${c['decisions']['cost_usd']} → {out}")
    return 0


@_errors
def cmd_update_check(args) -> int:
    from ontologizer.core import update_check as uc
    run = _run(args)
    engine = _engine(run.cfg)
    if engine is None:
        print("update-check reads the release from the store; no store answers", file=sys.stderr)
        return 2
    try:
        d = uc.check(run, _frame(run.cfg), engine, args.base, args.base_run)
    except uc.UpdateCheckError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"update-check @{args.base}: {d['kept']} kept, {len(d['changed'])} changed, {len(d['moved'])} moved, "
          f"{len(d['added'])} added, {len(d['removed'])} removed; {len(d['blocking'])} blocking → "
          f"{run.path('update-check.md')}")
    for x in d["blocking"]:
        print(f"- {x}")
    return 3 if d["blocking"] else 0


# ---- stage 9 ---------------------------------------------------------------------
@_errors
def cmd_overlay(args) -> int:
    from ontologizer.core import overlay
    from ontologizer.core.release_compare import load
    run = _run(args)
    if not run.overlay:
        print("refused: not an overlay run; set `process.overlay: true` in the target owner's config "
              "(docs/process.md §9)", file=sys.stderr)
        return 2
    fr = _frame(run.cfg)
    if bool(args.previous) != bool(args.compare):
        print("--previous and --compare go together: the earlier application file and the release "
              "comparison that translates its targets", file=sys.stderr)
        return 2
    prev = json.loads(Path(args.previous).read_text()) if args.previous else None
    comp = load(args.compare) if args.compare else None
    v = overlay.verdict(run, fr, _engine(run.cfg), previous=prev, comparison=comp)
    out = overlay.write(run, v)
    for t, tv in v["targets"].items():
        s = tv["summary"]
        print(f"{t}: {s['mapped']}/{s['concepts']} concepts mapped, realizes {s['realized']}/{s['shared']} shared "
              f"concepts" + (f"; changes {tv['changes']['counts']}" if "changes" in tv else ""))
    print(f"overlay: {out}")
    return 0


# ---- stage 8 ---------------------------------------------------------------------
@_errors
def cmd_review(args) -> int:
    from ontologizer.core import review
    run = _run(args)
    why = _not_overlay(run, "stage 8 (review)")
    if why:
        print(why, file=sys.stderr)
        return 2
    if args.action == "build":
        d = review.build(run, _frame(run.cfg), since=args.since)
        print(f"review: {d['items']} shared concepts, {d['relations']} relations in {d['path']}; {d['open']} need a "
              f"verdict ({d['kept']} kept, {d['carried']} carried from {args.since or 'no earlier run'}); "
              f"a strong agent gives them")
        return 0
    if not args.by:
        print("review apply needs --by NAME (the reviewing agent's model, e.g. claude-opus-5-5, or a person)",
              file=sys.stderr)
        return 2
    d = review.apply(run, args.by, effort=args.effort)
    print(f"review: {d['accept']} accepted ({d['accept'] - d.get('accepted_unconfirmed', 0)} asserted, {d.get('accepted_unconfirmed', 0)} unconfirmed stay hypotheses), {d['reject']} rejected, {d['revise']} to revise; "
          f"{d['mappings_removed']} project mappings removed, {d['relations_rejected']} relations rejected, "
          f"{d['carried']} verdicts carried; gate recorded")
    return 0


@_errors
def cmd_gold(args) -> int:
    from ontologizer.core import review
    run = _run(args)
    why = _not_overlay(run, "stage 8 (gold)")
    if why:
        print(why, file=sys.stderr)
        return 2
    if args.action == "sample":
        d = review.sample(run, args.target[0], size=args.size, seed=args.seed,
                          kinds=args.kinds.split(",") if args.kinds else None)
        print(f"gold: {d['size']} items in {d['path']} {d['by_kind']}; a strong agent that made none of these decisions labels them (labelled_by)")
        return 0
    d = review.calibrate(run, args.target if len(args.target) > 1 else args.target[0])
    print(f"calibration: thresholds {d['thresholds']}, accuracy {d['accuracy']}, n {d['n']}"
          + (f" (provisional, not applied: {d['provisional']})" if d["provisional"] else ""))
    return 0


def register(sub) -> None:
    def add(name, fn, **kw):
        s = sub.add_parser(name, **kw)
        s.add_argument("--run", default=None, help="run id (default: process.run in the config)")
        s.set_defaults(fn=fn)
        return s
    s = add("frame", cmd_frame); s.add_argument("action", choices=["init", "check", "variation"])
    s = add("gate", cmd_gate); s.add_argument("verb", choices=["approve"]); s.add_argument("gate")
    s.add_argument("--by", required=True); s.add_argument("--note")
    s = add("inventory", cmd_inventory); s.add_argument("projects", nargs="*")
    s.add_argument("--rebuild-stale", action="store_true", help="rebuild stale source graphs when the adapter can")
    s = add("brief", cmd_brief); s.add_argument("kind", choices=["extract"]); s.add_argument("project")
    s.add_argument("--extractor", required=True); s.add_argument("--parts", type=int, default=1)
    s = add("reconcile", cmd_reconcile); s.add_argument("project"); s.add_argument("--apply", action="store_true")
    s = add("map", cmd_map); s.add_argument("project"); s.add_argument("--apply", action="store_true")
    add("group", cmd_group)
    s = add("verify", cmd_verify); s.add_argument("project"); s.add_argument("--apply", action="store_true")
    s.add_argument("--keep-failed", action="store_true", help="keep claims left without supporting evidence")
    add("stability", cmd_stability)
    s = add("report", cmd_report); s.add_argument("--compare", nargs=2, metavar=("A", "B"))
    s = add("release-compare", cmd_release_compare); s.add_argument("a", help="earlier version, e.g. 0.1")
    s.add_argument("b", help="later version built by --run, or `working` for the unreleased shared levels"); s.add_argument("--run-a", default=None,
                                                                              help="the run that built A, if any")
    s = add("update-check", cmd_update_check); s.add_argument("base", help="the release to hold the update to, e.g. 0.1")
    s.add_argument("--base-run", required=True, help="the run that built it, for its competency questions")
    s = add("overlay", cmd_overlay); s.add_argument("--previous", help="earlier application file of the target")
    s.add_argument("--compare", help="release-compare JSON from the earlier release to this one")
    s = add("review", cmd_review); s.add_argument("action", choices=["build", "apply"]); s.add_argument("--by")
    s.add_argument("--effort", help="the reviewing agent's reasoning effort, e.g. high")
    s.add_argument("--since", help="build: an earlier run whose accepted verdicts stand for what has not changed")
    s = add("gold", cmd_gold); s.add_argument("action", choices=["sample", "calibrate"])
    s.add_argument("target", nargs="+", help="sample: gold set name; calibrate: the labelled gold file(s)")
    s.add_argument("--size", type=int, default=None); s.add_argument("--seed", type=int, default=0)
    s.add_argument("--kinds", help="sample only these kinds, e.g. triage (comma-separated)")
