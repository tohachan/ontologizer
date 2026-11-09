"""Stage 6: verify every claim of a project and recheck disputed judgements (docs/process.md §6).

- Every evidence item of every claim (concepts, relations, variation choices,
  mappings) is re-read from the source at its commit. Missing files or lines
  fail mechanically. The decision port judges whether the lines support the
  claim (`noul`); in llm mode the answering model must differ from every model
  that wrote the extraction.
- Low-confidence and disputed judgements, and every `partial` mapping, are packed
  into `verify/<p>.recheck.json` for a model other than the one that made them:
  by default the rechecker of `process.recheck` (another vendor, e.g. Codex,
  docs/cross-harness.md), else its declared fallback; its verdicts come back in
  `verify/<p>.recheck.answers.json`.
- Absence claims carry their search protocol (enforced by the schema) and are listed.
- `--apply` drops unsupported evidence, removes claims left without any, and
  applies the recheck verdicts to `application/<p>.json`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import application_file
from ontologizer.core.sources import evidence_source
from ontologizer.ports.decisions import Question
from ontologizer.ports.evidence import git_version, read_lines, slice_lines

PAD = 3
LIMIT = 160


class VerifyError(ValueError):
    pass


UNKNOWN_COMMIT = object()   # the checkout does not have the cited commit: the item cannot be checked


def _has_commit(root: Path, commit: str) -> bool:
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True)
    return r.returncode == 0


def read_at(root: Path | None, commit: str | None, path: str, lines: str | None):
    """Cited lines at `commit` (git show); without a commit, from the working tree. None when the file
    or lines do not exist at that commit; UNKNOWN_COMMIT when the checkout lacks the commit."""
    if root is None:
        return UNKNOWN_COMMIT
    if commit and git_version(root) is not None:
        if not _has_commit(root, commit):
            return UNKNOWN_COMMIT
        try:   # `./`: relative to the source, which may be a directory inside a larger repository
            blob = subprocess.run(["git", "-C", str(root), "show", f"{commit}:./{path}"], capture_output=True,
                                  check=True).stdout.decode(errors="replace")
        except (OSError, subprocess.CalledProcessError):
            return None
        return slice_lines(blob.splitlines(), lines, pad=PAD, limit=LIMIT)
    return read_lines(root, path, lines, pad=PAD, limit=LIMIT)


def claims(doc: dict) -> list[dict]:
    p = doc["project"]
    label = {c["id"]: c["label"] for c in doc["concepts"]}
    out = []
    for c in doc["concepts"]:
        out.append({"id": f"concept:{c['id']}", "kind": "concept", "ref": c["id"],
                    "text": f"In the project {p}, '{c['label']}' ({c['upper_category']}) is: {c['definition']}",
                    "evidence": c.get("evidence", [])})
        for m in c.get("maps_to", []):
            if m.get("evidence"):
                out.append({"id": f"mapping:{c['id']}->{m.get('target')}", "kind": "mapping", "ref": c["id"],
                            "text": f"'{c['label']}' is a {m['kind']} of {m.get('target')}", "evidence": m["evidence"]})
    for r in doc.get("relations", []):
        f, t = label.get(r["from"], r["from"]), label.get(r["to"], r["to"])
        out.append({"id": f"relation:{r['from']}--{r['type']}--{r['to']}", "kind": "relation",
                    "text": f"In the project {p}, '{f}' {r['type']} '{t}'" + (f" ({r['label']})" if r.get("label") else ""),
                    "evidence": r.get("evidence", [])})
    for v in doc.get("variation", []):
        out.append({"id": f"variation:{v['dimension']}--{v['value']}", "kind": "variation",
                    "text": f"The project {p} takes value '{v['value']}' on dimension '{v['dimension']}'"
                            + (f": {v['note']}" if v.get("note") else ""), "evidence": v.get("evidence", [])})
    return out


def authors(doc: dict) -> list[str]:
    return sorted({e.get("model") for e in doc.get("extractors", []) if e.get("model")}
                  | {doc.get("reconciled", {}).get("model")} - {None})


def analyse(run: Run, project: str) -> dict:
    f = application_file(run.cfg, project)
    if not f.is_file():
        raise VerifyError(f"{project}: {f} missing")
    doc = json.loads(f.read_text())
    src = evidence_source(run.cfg)
    try:
        root = Path(src.source_dir(project)) if src else None
    except (KeyError, FileNotFoundError, OSError) as e:
        raise VerifyError(f"{project}: no source checkout from the evidence adapter: {e}")
    dec = run.decider("verify")
    if dec.mode == "jev":
        judge = getattr(run.adapter(), "model", None) or "jev"
    elif dec.mode == "llm":
        af = load_json(run.path("decisions", "verify.answers.json"), {}) or {}
        judge = af.get("model")
        if af.get("answers") and not judge:
            raise VerifyError(f"{project}: verify.answers.json needs `model`")
        if judge and judge in authors(doc):
            raise VerifyError(f"{project}: verify answers by {judge}, which also wrote the extraction; "
                              f"verification needs a different model")
    else:
        judge = None
    thr = run.threshold("support")
    rconf = run.threshold("recheck_confidence")
    commit0 = (doc.get("source") or {}).get("commit")
    items, low = [], []
    for cl in claims(doc):
        evs = cl["evidence"]
        if not evs:
            items.append({"claim": cl["id"], "evidence": None, "verdict": "no-evidence"})
            continue
        texts = []
        for k, e in enumerate(evs):
            if e.get("kind") in ("graph", "plan"):
                texts.append((k, e, None, "not-source"))
                continue
            t = read_at(root, e.get("commit") or commit0, e["path"], e.get("lines"))
            if t is UNKNOWN_COMMIT:
                texts.append((k, e, None, "unknown-commit"))
            else:
                texts.append((k, e, t, "ok" if t else "missing"))
        readable = [(k, e, t) for k, e, t, s in texts if s == "ok"]
        ans = {}
        if readable and dec.on:
            state = {"claim": cl["text"],
                     "evidence": [{"n": k, "path": e["path"], "lines": e.get("lines"), "text": t} for k, e, t in readable]}
            qs = [Question(f"e{k}", "noul", instructions=f"Do the lines of evidence n={k} support the claim? Answer "
                                                         f"by what the lines show, not by plausibility.")
                  for k, _, _ in readable]
            ans = dec.ask(state, qs)
        for k, e, t, s in texts:
            it = {"claim": cl["id"], "evidence": k, "path": e["path"], "lines": e.get("lines")}
            if s == "not-source":
                it["verdict"] = "not-source"
            elif s == "unknown-commit":
                it["verdict"] = "unchecked"
            elif s == "missing":
                it["verdict"] = "missing"
            elif not dec.on:
                it["verdict"] = "readable"
            else:
                a = ans.get(f"e{k}")
                if a is None or not a.ok:
                    it["verdict"] = "unchecked" if a is None else f"error:{a.error}"
                else:
                    it["p"] = round(float(a.value), 3)
                    it["verdict"] = "supported" if a.value >= thr else "unsupported"
                    if a.confidence is not None:
                        it["confidence"] = round(a.confidence, 3)
                    if (a.confidence is not None and a.confidence < rconf) or abs(float(a.value) - thr) < 0.15:
                        low.append({"id": f"evidence:{cl['id']}#e{k}", "type": "evidence", "claim": cl["text"],
                                    "judgement": it["verdict"], "p": it["p"], "author": judge,
                                    "evidence": {"path": e["path"], "lines": e.get("lines"), "text": t}})
            items.append(it)
    dec.done()
    rechecks = low + _judgement_rechecks(run, project, doc)
    conf = rechecker(run) or {}
    def authored(model, au) -> bool:
        return bool(model) and (au == model or (isinstance(au, list) and model in au))

    for it in rechecks:   # the default rechecker also authored this item (e.g. it is an extractor): the fallback answers
        au = it.get("author")
        if authored(conf.get("model"), au):
            fb = conf.get("fallback") or {}
            # the fallback made it too (every extractor wrote the concept): no model may recheck it
            it["recheck_by"] = NO_RECHECKER if authored(fb.get("model"), au) else (fb.get("model") or "another model")
    checked = [i for i in items if i["verdict"] in ("supported", "unsupported", "missing")]
    supported = sum(1 for i in checked if i["verdict"] == "supported")
    out = {"project": project, "run": run.id, "at": now(), "mode": dec.mode, "threshold": thr,
           "authors": authors(doc), "source_dir": str(root) if root else None,
           "evidence": {"items": len([i for i in items if i["evidence"] is not None]), "checked": len(checked),
                        "supported": supported, "unsupported": sum(1 for i in checked if i["verdict"] == "unsupported"),
                        "missing": sum(1 for i in checked if i["verdict"] == "missing"),
                        "unchecked": sum(1 for i in items if i["verdict"] in ("readable", "unchecked")
                                         or str(i["verdict"]).startswith("error")),
                        "share": round(supported / len(checked), 3) if checked and dec.on else None},
           "claims_without_evidence": [i["claim"] for i in items if i["verdict"] == "no-evidence"],
           "absences": doc.get("absences", []), "items": items, "rechecks": len(rechecks)}
    save_json(run.path("verify", f"{project}.json"), out)
    save_json(run.path("verify", f"{project}.recheck.json"),
              {"project": project, "run": run.id, "instructions": RECHECK_HELP, "items": rechecks})
    return out


def final_items(run: Run, project: str, v: dict | None = None) -> list[dict]:
    """The evidence items with the verdict that counts: once the verification is applied, a recheck overrides
    the first judgement. Each keeps its first verdict in `verdict` and gets `final`."""
    v = v if v is not None else (load_json(run.path("verify", f"{project}.json"), {}) or {})
    answers = {}
    if v.get("applied"):
        answers = (load_json(run.path("verify", f"{project}.recheck.answers.json"), {}) or {}).get("verdicts") or {}
    out = []
    for it in v.get("items", []):
        r = answers.get(f"evidence:{it['claim']}#e{it['evidence']}") if it.get("evidence") is not None else None
        out.append({**it, "final": r.get("value") if r and r.get("verdict") == "change" else it["verdict"]})
    return out


def _judgement_rechecks(run: Run, project: str, doc: dict) -> list[dict]:
    m = load_json(run.path("map", f"{project}.json"), {}) or {}
    map_model = (load_json(run.path("map", f"{project}.answers.json"), {}) or {}).get("model")
    disputed_cat = set((m.get("category_disagreement") or {}).get("ids", []))
    out = []
    for c in doc["concepts"]:
        ctx = {"label": c["label"], "definition": c["definition"], "evidence": c.get("evidence", [])[:3]}
        for x in c.get("maps_to", []):
            if x.get("confidence") == "low" or x.get("status") == "disputed" or x.get("kind") == "partial":
                out.append({"id": f"mapping:{c['id']}->{x.get('target')}", "type": "mapping", "concept": ctx,
                            "judgement": {"target": x.get("target"), "kind": x.get("kind"), "note": x.get("note")},
                            "author": map_model})
        if c["id"] in disputed_cat:
            out.append({"id": f"category:{c['id']}", "type": "category", "concept": ctx,
                        "judgement": {"upper_category": c["upper_category"]}, "author": map_model,
                        "second_opinion": next((i.get("category_opinion") for i in m.get("concepts", [])
                                                if i["id"] == c["id"]), None)})
        if c.get("status") == "disputed":
            out.append({"id": f"status:{c['id']}", "type": "disputed-claim", "concept": ctx,
                        "judgement": {"status": "disputed", "note": c.get("note")}, "author": authors(doc)})
    return out


NO_RECHECKER = "none-available"   # recheck_by of an item every rechecker authored: kept as judged, counted apart

RECHECK_HELP = (
    "You are a different model from the one that made each judgement (see `author`). An item with `recheck_by` "
    "is answered by that model instead: put its verdict with `\"model\": \"<that model>\"` inside the verdict "
    f"(`recheck_by: {NO_RECHECKER}` needs no answer: every rechecker made that judgement, so it is kept as it is). "
    "For every item, read the "
    "evidence (and the source when needed) and write verify/<project>.recheck.answers.json as {\"model\": \"<id>\", "
    "\"harness\": \"claude|codex\", \"run_id\": \"<session or stream id>\", \"verdicts\": {\"<item id>\": "
    "{\"verdict\": \"confirm\" | \"change\", \"value\": <new value when change: evidence → \"supported\"|\"unsupported\"; "
    "mapping → {\"target\", \"kind\"} or null for none; category → \"<category>\"; disputed-claim → "
    "\"hypothesis\"|\"disputed\">, \"reason\": \"...\"}}}.")


def rechecker(run: Run) -> dict | None:
    """`process.recheck`: the default rechecker of stages 4 and 6, a different vendor
    (`{harness: codex, model: …}`), and its `fallback` when that harness is unavailable."""
    return run.process.get("recheck")


def recheck_policy(run: Run, answers: dict) -> str | None:
    """Which rechecker made the answers: `default`, `fallback` (needs `fallback_reason`), `other`,
    or None without a configured policy."""
    conf = rechecker(run)
    if not conf:
        return None

    def same(c):
        return bool(c) and c.get("harness") == answers.get("harness") and \
            (not c.get("model") or c.get("model") == answers.get("model"))
    if same(conf):
        return "default"
    if same(conf.get("fallback")):
        if not answers.get("fallback_reason"):
            raise VerifyError("the recheck ran on the fallback; say why the default was not used in `fallback_reason`")
        return "fallback"
    return "other"


def apply(run: Run, project: str, keep_failed: bool = False) -> dict:
    v = load_json(run.path("verify", f"{project}.json"))
    if v is None:
        raise VerifyError(f"{project}: run `ontologizer verify {project}` first")
    if v.get("applied"):
        raise VerifyError(f"{project}: verification of run {run.id} was already applied at {v['applied']['at']}; "
                          f"run `ontologizer verify {project}` again before applying")
    rc = load_json(run.path("verify", f"{project}.recheck.json"), {"items": []})
    ra = load_json(run.path("verify", f"{project}.recheck.answers.json"), {}) or {}
    verdicts = ra.get("verdicts") or {}
    if rc["items"] and not ra.get("model"):
        raise VerifyError(f"{project}: the recheck answers need `model` (and `harness`, `run_id`)")
    unknown = [i["id"] for i in rc["items"] if not i.get("author")]
    if unknown:
        raise VerifyError(f"{project}: recheck items without a known author {unknown[:5]}; the judgement's model "
                          f"must be recorded (e.g. `model` in map/<p>.answers.json) so another model rechecks it")
    blind = [i["id"] for i in rc["items"] if i.get("recheck_by") == NO_RECHECKER]
    for k in blind:      # no independent model exists for it: the current judgement stands, whatever was answered
        verdicts[k] = {"verdict": "confirm", "reason": "no rechecker available: every rechecker authored it",
                       "model": NO_RECHECKER}
    missing = [i["id"] for i in rc["items"] if not (verdicts.get(i["id"]) or {}).get("reason")]
    if missing:
        raise VerifyError(f"{project}: {len(missing)} recheck item(s) without a verdict and reason in "
                          f"{run.path('verify', project + '.recheck.answers.json')}: {missing[:8]}")
    def judge_of(item_id: str) -> str | None:   # a verdict may name its own model (an item every main model authored)
        return (verdicts.get(item_id) or {}).get("model") or ra.get("model")

    same = [i["id"] for i in rc["items"] if i["id"] not in blind and (
            judge_of(i["id"]) == i["author"] or (isinstance(i["author"], list) and judge_of(i["id"]) in i["author"]))]
    if same:
        raise VerifyError(f"{project}: rechecked by {ra.get('model')}, the model that made {same[:5]}")
    policy = recheck_policy(run, ra) if rc["items"] else None
    if policy == "other":
        raise VerifyError(f"{project}: rechecked by {ra.get('harness')}/{ra.get('model')}, which is neither the default "
                          f"rechecker nor its fallback (process.recheck: {run.process.get('recheck')})")
    f = application_file(run.cfg, project)
    doc = json.loads(f.read_text())
    # evidence verdicts (recheck overrides)
    bad: dict[str, set[int]] = {}
    for it in v["items"]:
        if it["evidence"] is None:
            continue
        verdict = it["verdict"]
        r = verdicts.get(f"evidence:{it['claim']}#e{it['evidence']}")
        if r and r["verdict"] == "change":
            verdict = r.get("value")
        if verdict in ("unsupported", "missing"):
            bad.setdefault(it["claim"], set()).add(it["evidence"])
    removed, dropped_ev = [], 0

    def prune(cid: str, evs: list) -> list:
        nonlocal dropped_ev
        drop = bad.get(cid, set())
        dropped_ev += len(drop)
        return [e for k, e in enumerate(evs) if k not in drop]

    keep_concepts, undisputed = [], []
    for c in doc["concepts"]:
        cid = f"concept:{c['id']}"
        had = bool(c.get("evidence"))
        c["evidence"] = prune(cid, c.get("evidence", []))
        for m in c.get("maps_to", []):
            if m.get("evidence"):
                m["evidence"] = prune(f"mapping:{c['id']}->{m.get('target')}", m["evidence"])
                if not m["evidence"]:
                    m.pop("evidence")
        if had and not c["evidence"] and not keep_failed:
            removed.append(cid)
            continue
        if c.get("status") == "disputed" and len(c["evidence"]) < 2:
            # one reading lost its evidence: nothing is left to dispute (rule 11.4 needs both readings)
            c["status"] = "hypothesis"
            c["note"] = (c.get("note", "") + f" Dispute dropped in stage 6 ({run.id}): the evidence of one reading "
                         "did not hold.").strip()
            undisputed.append(cid)
        keep_concepts.append(c)
    gone = {r.split(":", 1)[1] for r in removed}
    doc["concepts"] = keep_concepts
    for c in doc["concepts"]:
        if "broader" in c:
            c["broader"] = [b for b in c["broader"] if b not in gone]
    rels = []
    for r in doc.get("relations", []):
        rid = f"relation:{r['from']}--{r['type']}--{r['to']}"
        if r["from"] in gone or r["to"] in gone:
            continue
        had = bool(r.get("evidence"))
        r["evidence"] = prune(rid, r.get("evidence", []))
        if had and not r["evidence"] and not keep_failed:
            removed.append(rid)
            continue
        rels.append(r)
    doc["relations"] = rels
    var = []
    for x in doc.get("variation", []):
        vid = f"variation:{x['dimension']}--{x['value']}"
        had = bool(x.get("evidence"))
        x["evidence"] = prune(vid, x.get("evidence", []))
        if had and not x["evidence"] and not keep_failed:
            removed.append(vid)
            continue
        var.append(x)
    if "variation" in doc:
        doc["variation"] = var
    # judgement rechecks
    changed = 0
    by_id = {c["id"]: c for c in doc["concepts"]}
    for it in rc["items"]:
        r = verdicts[it["id"]]
        if r["verdict"] != "change" or it["type"] == "evidence":
            continue
        cid = it["id"].split(":", 1)[1].split("->")[0]
        c = by_id.get(cid)
        if c is None:
            continue
        changed += 1
        note = f"Rechecked in stage 6 by {r.get('model') or ra.get('model')}: {r['reason']}"
        if it["type"] == "mapping":
            tgt = it["judgement"]["target"]
            c["maps_to"] = [m for m in c.get("maps_to", []) if m.get("target") != tgt]
            if r.get("value"):
                c["maps_to"].append({"target": r["value"]["target"], "kind": r["value"]["kind"],
                                     "confidence": "medium", "note": note})
        elif it["type"] == "category":
            from ontologizer.core.levels import Preset
            if r["value"] not in Preset.load(run.cfg.preset_path).categories:
                raise VerifyError(f"{it['id']}: unknown category {r['value']!r}")
            c["upper_category"] = r["value"]
            c["note"] = (c.get("note", "") + " " + note).strip()
        elif it["type"] == "disputed-claim":
            c["status"] = r["value"]
            c["note"] = (c.get("note", "") + " " + note).strip()
    doc.setdefault("reconciled", {})["verified"] = {"run": run.id, "recheck_model": ra.get("model"),
                                                    "recheck_harness": ra.get("harness")}
    save_json(f, doc)
    by_type: dict[str, dict] = {}
    for it in rc["items"]:
        t = by_type.setdefault(it["type"], {"rechecked": 0, "changed": 0})
        t["rechecked"] += 1
        t["changed"] += verdicts[it["id"]]["verdict"] == "change"
    mp = [it for it in rc["items"] if it["type"] == "mapping" and it["judgement"].get("kind") == "partial"]
    if mp:
        by_type["mapping-partial"] = {"rechecked": len(mp),
                                      "changed": sum(verdicts[it["id"]]["verdict"] == "change" for it in mp)}
    for t in by_type.values():
        t["change_rate"] = round(t["changed"] / t["rechecked"], 3) if t["rechecked"] else None
    stats = {"removed_claims": removed, "dropped_evidence": dropped_ev, "rechecks_changed": changed,
             "no_rechecker": blind,
             "recheck_by_type": by_type,
             "undisputed": undisputed,
             "recheck": {"model": ra.get("model"), "harness": ra.get("harness"), "run_id": ra.get("run_id"),
                         "items": len(rc["items"]), "policy": policy,
                         **({"fallback_reason": ra.get("fallback_reason")} if policy == "fallback" else {})}}
    v["applied"] = {"at": now(), **stats}
    save_json(run.path("verify", f"{project}.json"), v)
    return stats
