"""Stage 6: verify every claim of a project and recheck disputed judgements (docs/process.md §6).

- Every evidence item of every claim (concepts, relations, variation choices,
  mappings) is re-read from the source at its commit. Missing files or lines
  fail mechanically. The decision port judges whether the lines support the
  claim (`noul`); in llm mode the answering model must differ from every model
  that wrote the extraction.
- Every negative evidence judgement, low-confidence judgements, disputed
  judgements and every `partial` mapping are packed into `verify/<p>.recheck.json`
  for a model other than the one that made them: by default the rechecker of
  `process.recheck` (another vendor, e.g. Codex, docs/cross-harness.md), else its
  declared fallback; its verdicts come back in `verify/<p>.recheck.answers.json`.
  Mappings are shown with the target's current shared definition.
- A recheck verdict given on the same content as before is carried into the new
  pack, so it is not asked again. `--changed` also keeps the decision port's
  earlier judgement of every evidence item whose claim and citation are unchanged.
- Absence claims carry their search protocol (enforced by the schema) and are listed.
- `--apply` drops evidence whose negative judgement a recheck (or the owner, in
  `verify/<p>.owner.json`) confirmed and evidence whose lines are missing, removes
  claims left without any, records every removed claim with its reason and its
  original form (relations removed with an end included), keeps unconfirmed
  negatives as `unverified`, and applies the recheck verdicts to `application/<p>.json`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from ontologizer.core.run import Run, load_json, now, save_json
from ontologizer.core.shared import LEVELS, application_file, local_shared_file
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


def _key(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def evidence_key(claim_text: str, e: dict, commit0: str | None) -> str:
    """What an evidence judgement is about: the claim as worded and the lines it cites. Equal keys
    mean the judgement can be kept (`verify --changed`)."""
    return _key([claim_text, e.get("path"), e.get("lines"), e.get("commit") or commit0, e.get("kind")])


_VOLATILE = {"id", "key", "carried", "recheck_by", "p"}


def recheck_key(item: dict) -> str:
    """What a recheck verdict is about. An evidence item's id is its position in the claim, which
    changes when other evidence is dropped, so its key is built from the content alone."""
    stable = {k: v for k, v in item.items() if k not in _VOLATILE}
    if item.get("type") != "evidence":
        stable["id"] = item["id"]
    return _key(stable)


def shared_targets(run: Run, project: str) -> tuple[dict[str, dict], str | None]:
    """The shared concepts a mapping may point at, with the wording they have now, and where it
    comes from: the owner's shared-level files as they are (after consolidation), or, for an overlay
    target without such files, the release read from the store in stage 4 (`map/<p>.json`)."""
    out, files = {}, []
    for lv in LEVELS:
        p = local_shared_file(run.cfg, lv)
        if p is None:
            continue
        for c in json.loads(p.read_text()).get("concepts", []):
            out[f"{lv}:{c['id']}"] = {"label": c.get("label"), "definition": c.get("definition"),
                                      "upper_category": c.get("upper_category")}
        try:
            files.append(str(p.relative_to(run.cfg.root)))
        except ValueError:
            files.append(str(p))
    if files:
        return out, f"current shared-level files ({', '.join(files)}) as of this verify"
    m = load_json(run.path("map", f"{project}.json"), {}) or {}
    if m.get("shared"):
        return ({x["ref"]: {"label": x.get("label"), "definition": x.get("definition"),
                            "upper_category": x.get("category")} for x in m["shared"]},
                f"release graphs {m.get('shared_graphs')} as read in stage 4 ({m.get('at')})")
    return {}, None


def analyse(run: Run, project: str, changed: bool = False, since: str | None = None) -> dict:
    """`changed`: keep the earlier evidence judgements of this run's last verify (or of run `since`)
    for every claim and citation that has not changed, and ask the decision port only about the rest."""
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
    prev_run = Run(run.cfg, since) if since else run
    earlier: dict[tuple[str, str], dict] = {}
    if changed or since:
        prev = load_json(prev_run.path("verify", f"{project}.json"))
        if prev is None:
            raise VerifyError(f"{project}: no earlier verification in run {prev_run.id} to keep; "
                              f"run `ontologizer verify {project}` first")
        if prev.get("mode") != dec.mode:
            raise VerifyError(f"{project}: the earlier verification ran in mode {prev.get('mode')}, this one in "
                              f"{dec.mode}; run a full `ontologizer verify {project}`")
        earlier = {(i["claim"], i["key"]): i for i in prev.get("items", []) if i.get("key") and "p" in i}
    thr = run.threshold("support")
    rconf = run.threshold("recheck_confidence")
    commit0 = (doc.get("source") or {}).get("commit")
    items, low = [], []
    kept = 0
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
        keys = {k: evidence_key(cl["text"], e, commit0) for k, e in enumerate(evs)}
        prior = {k: earlier[(cl["id"], keys[k])] for k, _, _, s in texts
                 if s == "ok" and (cl["id"], keys[k]) in earlier}
        ask = [(k, e, t) for k, e, t, s in texts if s == "ok" and k not in prior]
        ans = {}
        if ask and dec.on:
            state = {"claim": cl["text"],
                     "evidence": [{"n": k, "path": e["path"], "lines": e.get("lines"), "text": t} for k, e, t in ask]}
            qs = [Question(f"e{k}", "noul", instructions=f"Do the lines of evidence n={k} support the claim? Answer "
                                                         f"by what the lines show, not by plausibility.")
                  for k, _, _ in ask]
            ans = dec.ask(state, qs)
        for k, e, t, s in texts:
            it = {"claim": cl["id"], "evidence": k, "path": e["path"], "lines": e.get("lines"), "key": keys[k]}
            if s == "not-source":
                it["verdict"] = "not-source"
            elif s == "unknown-commit":
                it["verdict"] = "unchecked"
            elif s == "missing":
                it["verdict"] = "missing"
            elif not dec.on:
                it["verdict"] = "readable"
            else:
                pr = prior.get(k)
                if pr is not None:
                    p, conf, who = float(pr["p"]), pr.get("confidence"), pr.get("judge") or judge
                    it["kept_from"] = prev_run.id
                    kept += 1
                else:
                    a = ans.get(f"e{k}")
                    if a is None or not a.ok:
                        it["verdict"] = "unchecked" if a is None else f"error:{a.error}"
                        items.append(it)
                        continue
                    p, conf, who = float(a.value), a.confidence, judge
                it["p"] = round(p, 3)
                it["judge"] = who
                it["verdict"] = "supported" if p >= thr else "unsupported"
                if conf is not None:
                    it["confidence"] = round(conf, 3)
                # every negative judgement is rechecked: a classifier's score alone never removes a claim
                if it["verdict"] == "unsupported" or (conf is not None and conf < rconf) or abs(p - thr) < 0.15:
                    low.append({"id": f"evidence:{cl['id']}#e{k}", "type": "evidence", "claim": cl["text"],
                                "claim_id": cl["id"], "evidence_key": keys[k],
                                "judgement": it["verdict"], "p": it["p"], "author": who,
                                "evidence": {"path": e["path"], "lines": e.get("lines"), "text": t}})
            items.append(it)
    dec.done()
    targets, targets_from = shared_targets(run, project)
    rechecks = low + _judgement_rechecks(run, project, doc, targets, targets_from)
    conf = rechecker(run) or {}

    def authored(model, au) -> bool:
        return bool(model) and (au == model or (isinstance(au, list) and model in au))

    for it in rechecks:   # the default rechecker also authored this item (e.g. it is an extractor): the fallback answers
        au = it.get("author")
        if authored(conf.get("model"), au):
            fb = conf.get("fallback") or {}
            # the fallback made it too (every extractor wrote the concept): no model may recheck it
            it["recheck_by"] = NO_RECHECKER if authored(fb.get("model"), au) else (fb.get("model") or "another model")
    # a recheck verdict given on the same content stands: it is carried, not asked again
    prior_verdicts = _prior_recheck_verdicts(prev_run, project) if prev_run is not run else {}
    prior_verdicts.update(_prior_recheck_verdicts(run, project))
    carried = 0
    for it in rechecks:
        it["key"] = recheck_key(it)
        pv = prior_verdicts.get(it["key"])
        if pv and it.get("recheck_by") != NO_RECHECKER:
            it["carried"] = pv
            carried += 1
    old = run.path("verify", f"{project}.recheck.answers.json")
    if old.is_file():   # its ids are positions in the earlier pack; what still holds is carried above
        old.replace(run.path("verify", f"{project}.recheck.answers.prev.json"))
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
           "absences": doc.get("absences", []), "items": items, "rechecks": len(rechecks),
           "kept": {"from": prev_run.id, "evidence": kept} if (changed or since) else None,
           "carried_rechecks": carried, "to_answer": len(rechecks) - carried
           - sum(1 for i in rechecks if i.get("recheck_by") == NO_RECHECKER)}
    save_json(run.path("verify", f"{project}.json"), out)
    save_json(run.path("verify", f"{project}.recheck.json"),
              {"project": project, "run": run.id, "at": out["at"], "instructions": RECHECK_HELP,
               "shared_definitions": targets_from, "items": rechecks})
    return out


def _prior_recheck_verdicts(run: Run, project: str) -> dict[str, dict]:
    """The verdicts of a run's last recheck pack by item key: answered there, or carried into it."""
    rc = load_json(run.path("verify", f"{project}.recheck.json"), {}) or {}
    ra = load_json(run.path("verify", f"{project}.recheck.answers.json"), {}) or {}
    answers = ra.get("verdicts") or {}
    out = {}
    for it in rc.get("items", []):
        if not it.get("key") or it.get("recheck_by") == NO_RECHECKER:
            continue
        r = answers.get(it["id"])
        if r and r.get("reason") and r.get("verdict") in ("confirm", "change") and ra.get("model"):
            out[it["key"]] = {**r, "model": r.get("model") or ra.get("model"), "from": f"{run.id} {rc.get('at')}",
                              "pack": {k: ra.get(k) for k in ("model", "harness", "run_id", "fallback_reason")
                                       if ra.get(k) is not None}}
        elif it.get("carried"):
            out[it["key"]] = it["carried"]
    return out


def owner_decisions(run: Run, project: str) -> dict:
    """`verify/<p>.owner.json`, written by the owner, never by an agent: `{"by": "<owner>", "verdicts":
    [{"claim": "<claim id>", "path": …, "lines": …, "verdict": "supported" | "unsupported", "reason": …}]}`.
    An entry without `path` covers every evidence item of the claim. It overrides every judgement."""
    d = load_json(run.path("verify", f"{project}.owner.json"), {}) or {}
    vs = d.get("verdicts") or []
    if not vs:
        return {}
    errs = [] if d.get("by") else ["`by` (the owner) required"]
    for n, x in enumerate(vs):
        if not x.get("claim") or x.get("verdict") not in ("supported", "unsupported") or not x.get("reason"):
            errs.append(f"verdicts[{n}]: needs `claim`, `verdict` supported|unsupported and `reason`")
    if errs:
        raise VerifyError(f"{project}: {run.path('verify', project + '.owner.json')}: " + "; ".join(errs))
    return d


def _owner_for(owner: dict, it: dict) -> dict | None:
    for x in owner.get("verdicts") or []:
        if x["claim"] == it["claim"] and (not x.get("path") or (
                x["path"] == it.get("path") and (x.get("lines") is None or x.get("lines") == it.get("lines")))):
            return x
    return None


def _verdicts(run: Run, project: str, rc: dict | None = None) -> tuple[dict, dict, dict]:
    """Each recheck item's verdict (answered now, else carried), where it came from, and the answers file."""
    rc = rc if rc is not None else (load_json(run.path("verify", f"{project}.recheck.json"), {}) or {})
    ra = load_json(run.path("verify", f"{project}.recheck.answers.json"), {}) or {}
    answered = ra.get("verdicts") or {}
    verdicts, origin = {}, {}
    for i in rc.get("items", []):
        if i.get("recheck_by") == NO_RECHECKER:
            # no independent model exists for it: the current judgement stands, whatever was answered
            verdicts[i["id"]] = {"verdict": "confirm", "reason": "no rechecker available: every rechecker authored it",
                                 "model": NO_RECHECKER}
            origin[i["id"]] = "blind"
        elif answered.get(i["id"]):
            verdicts[i["id"]] = {**answered[i["id"]], "model": answered[i["id"]].get("model") or ra.get("model")}
            origin[i["id"]] = "answered"
        elif i.get("carried"):
            verdicts[i["id"]] = i["carried"]
            origin[i["id"]] = "carried"
    return verdicts, origin, ra


def final_items(run: Run, project: str, v: dict | None = None) -> list[dict]:
    """The evidence items with the verdict that counts: once the verification is applied, a recheck
    (answered or carried) or the owner overrides the first judgement. Each keeps its first verdict in
    `verdict` and gets `final`; a negative judgement nobody confirmed also gets `unconfirmed: true`."""
    v = v if v is not None else (load_json(run.path("verify", f"{project}.json"), {}) or {})
    answers, owner, unconfirmed = {}, {}, set()
    if v.get("applied"):
        rc = load_json(run.path("verify", f"{project}.recheck.json"))
        answers = _verdicts(run, project, rc)[0] if rc is not None else \
            (load_json(run.path("verify", f"{project}.recheck.answers.json"), {}) or {}).get("verdicts") or {}
        try:
            owner = owner_decisions(run, project)
        except VerifyError:
            owner = {}
        unconfirmed = {u["id"] for u in v["applied"].get("unverified", [])}
    out = []
    for it in v.get("items", []):
        eid = f"evidence:{it['claim']}#e{it['evidence']}" if it.get("evidence") is not None else None
        r = answers.get(eid) if eid else None
        own = _owner_for(owner, it) if eid else None
        final = own["verdict"] if own else (r.get("value") if r and r.get("verdict") == "change" else it["verdict"])
        out.append({**it, "final": final, **({"unconfirmed": True} if eid in unconfirmed else {})})
    return out


def _judgement_rechecks(run: Run, project: str, doc: dict, targets: dict | None = None,
                        targets_from: str | None = None) -> list[dict]:
    if targets is None:
        targets, targets_from = shared_targets(run, project)
    m = load_json(run.path("map", f"{project}.json"), {}) or {}
    map_model = (load_json(run.path("map", f"{project}.answers.json"), {}) or {}).get("model")
    disputed_cat = set((m.get("category_disagreement") or {}).get("ids", []))
    out = []
    for c in doc["concepts"]:
        ctx = {"label": c["label"], "definition": c["definition"], "evidence": c.get("evidence", [])[:3]}
        for x in c.get("maps_to", []):
            if x.get("confidence") == "low" or x.get("status") == "disputed" or x.get("kind") == "partial":
                tgt = x.get("target")
                shown = ({"ref": tgt, **targets[tgt], "as_of": targets_from} if tgt in targets else
                         {"ref": tgt, "missing": f"not among the shared concepts ({targets_from or 'none found'})"})
                out.append({"id": f"mapping:{c['id']}->{tgt}", "type": "mapping", "concept": ctx,
                            "target": shown,
                            "judgement": {"target": tgt, "kind": x.get("kind"), "note": x.get("note")},
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
    "An item with `carried` was answered before on the same content; it needs no answer, and an answer you give "
    "replaces it. "
    "For every item, read the "
    "evidence (and the source when needed) and write verify/<project>.recheck.answers.json as {\"model\": \"<id>\", "
    "\"harness\": \"claude|codex\", \"run_id\": \"<session or stream id>\", \"verdicts\": {\"<item id>\": "
    "{\"verdict\": \"confirm\" | \"change\", \"value\": <new value when change: evidence → \"supported\"|\"unsupported\"; "
    "mapping → {\"target\", \"kind\"} or null for none, and with kind partial also \"overlap\": \"what both cover\" "
    "and \"differs\": \"what one has that the other does not\"; category → \"<category>\"; disputed-claim → "
    "\"hypothesis\"|\"disputed\">, \"reason\": \"...\"}}}. "
    "Evidence items judged `unsupported` are removed only when you confirm them; one you leave unanswered keeps "
    "its claim, marked unverified. "
    "Judge a mapping against `target.definition`: the shared concept as it is now (`as_of` says which version); "
    "the mapping's `note` was written in stage 4 and may quote an earlier wording.")


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


UNVERIFIED = "Stage 6 unverified:"


def _flag(obj: dict, text: str | None) -> None:
    """Set (text) or clear (None) the unverified marker at the end of an object's note."""
    note = (obj.get("note") or "").split(UNVERIFIED)[0].strip()
    if text:
        note = (note + f" {UNVERIFIED} {text}").strip()
    if note:
        obj["note"] = note
    else:
        obj.pop("note", None)


def _mapping_errors(run: Run, rc: dict, verdicts: dict, doc: dict) -> list[str]:
    """A mapping the recheck changes is held to the rules of `map --apply` (docs/process.md §4)."""
    from ontologizer.core.levels import MAPPING_KINDS, Preset
    preset = Preset.load(run.cfg.preset_path)
    allowed: dict[tuple[str, str], set] = {}
    for s, t, k in preset.allowed_mappings():
        allowed.setdefault((s, t), set()).add(k)
    known, known_from = shared_targets(run, doc["project"])
    level = {c["id"]: c.get("level", "application") for c in doc["concepts"]}
    errs = []
    for it in rc["items"]:
        r = verdicts.get(it["id"]) or {}
        if it["type"] != "mapping" or r.get("verdict") != "change" or not r.get("value"):
            continue
        val, cid = r["value"], it["id"].split(":", 1)[1].split("->")[0]
        if not isinstance(val, dict):
            errs.append(f"{it['id']}: value must be {{target, kind}} or null")
            continue
        t, k = val.get("target"), val.get("kind")
        if known_from and t not in known:
            errs.append(f"{it['id']}: unknown target {t!r}")
        elif k not in MAPPING_KINDS or (t and k not in allowed.get((level.get(cid), str(t).split(":")[0]), set())):
            errs.append(f"{it['id']}: kind {k!r} not allowed from {level.get(cid)} to {str(t).split(':')[0]}")
        if k == "partial" and not (str(val.get("overlap") or "").strip() and str(val.get("differs") or "").strip()):
            errs.append(f"{it['id']}: partial mapping to {t} needs `overlap` and `differs` in the value")
    return errs


def apply(run: Run, project: str, keep_failed: bool = False) -> dict:
    v = load_json(run.path("verify", f"{project}.json"))
    if v is None:
        raise VerifyError(f"{project}: run `ontologizer verify {project}` first")
    if v.get("applied"):
        raise VerifyError(f"{project}: verification of run {run.id} was already applied at {v['applied']['at']}; "
                          f"run `ontologizer verify {project}` again before applying (`--changed` judges only "
                          f"the claims that are new or changed since)")
    rc = load_json(run.path("verify", f"{project}.recheck.json"), {"items": []})
    verdicts, origin, ra = _verdicts(run, project, rc)
    fresh = [i for i in rc["items"] if origin.get(i["id"]) == "answered"]
    if fresh and not ra.get("model"):
        raise VerifyError(f"{project}: the recheck answers need `model` (and `harness`, `run_id`)")
    unknown = [i["id"] for i in rc["items"] if not i.get("author")]
    if unknown:
        raise VerifyError(f"{project}: recheck items without a known author {unknown[:5]}; the judgement's model "
                          f"must be recorded (e.g. `model` in map/<p>.answers.json) so another model rechecks it")
    blind = [i["id"] for i in rc["items"] if origin.get(i["id"]) == "blind"]
    # a negative evidence judgement may stay unanswered: its claim is then kept, unverified
    optional = {i["id"] for i in rc["items"] if i["type"] == "evidence" and i.get("judgement") == "unsupported"}
    missing = [i["id"] for i in rc["items"] if i["id"] not in optional and not (verdicts.get(i["id"]) or {}).get("reason")]
    if missing:
        raise VerifyError(f"{project}: {len(missing)} recheck item(s) without a verdict and reason in "
                          f"{run.path('verify', project + '.recheck.answers.json')}: {missing[:8]}")
    unanswered = [i for i in optional if not (verdicts.get(i) or {}).get("reason")]
    for i in unanswered:
        verdicts.pop(i, None)

    def judge_of(item_id: str) -> str | None:   # a verdict may name its own model (an item every main model authored)
        return (verdicts.get(item_id) or {}).get("model")

    same = [i["id"] for i in rc["items"] if i["id"] in verdicts and i["id"] not in blind and (
            judge_of(i["id"]) == i["author"] or (isinstance(i["author"], list) and judge_of(i["id"]) in i["author"]))]
    if same:
        raise VerifyError(f"{project}: rechecked by {judge_of(same[0])}, the model that made {same[:5]}")
    policy = recheck_policy(run, ra) if fresh else None
    if policy == "other":
        raise VerifyError(f"{project}: rechecked by {ra.get('harness')}/{ra.get('model')}, which is neither the default "
                          f"rechecker nor its fallback (process.recheck: {run.process.get('recheck')})")
    carried = [i["id"] for i in rc["items"] if origin.get(i["id"]) == "carried" and i["id"] in verdicts]
    for i in carried:
        if recheck_policy(run, verdicts[i].get("pack") or {}) == "other":
            raise VerifyError(f"{project}: the carried verdict of {i} was given by {verdicts[i].get('pack')}, "
                              f"which is neither the default rechecker nor its fallback")
    owner = owner_decisions(run, project)
    f = application_file(run.cfg, project)
    doc = json.loads(f.read_text())
    errs = _mapping_errors(run, rc, verdicts, doc)
    if errs:
        raise VerifyError(f"{project}: " + "; ".join(errs[:15]) + (f" (+{len(errs) - 15})" if len(errs) > 15 else ""))
    # evidence verdicts: a negative one counts only when a recheck or the owner confirmed it
    bad: dict[str, dict[int, str]] = {}
    unv: dict[str, dict[int, str]] = {}
    unverified = []
    for it in v["items"]:
        if it["evidence"] is None:
            continue
        eid = f"evidence:{it['claim']}#e{it['evidence']}"
        first, r, own = it["verdict"], verdicts.get(eid), _owner_for(owner, it)
        if own:
            final, why = own["verdict"], f"owner {owner['by']}: {own['reason']}"
        elif r and origin.get(eid) != "blind" and r["verdict"] == "change":
            final, why = r.get("value"), f"recheck by {r.get('model')} changed {first} to {r.get('value')}: {r['reason']}"
        elif r and origin.get(eid) != "blind":
            final, why = first, f"{first} (p={it.get('p')}, {it.get('judge')}), confirmed by {r.get('model')}: {r['reason']}"
        else:
            final, why = first, None
        if final == "missing":
            bad.setdefault(it["claim"], {})[it["evidence"]] = why or "the cited lines are missing at the cited commit"
        elif final == "unsupported" and why:
            bad.setdefault(it["claim"], {})[it["evidence"]] = why
        elif final == "unsupported":
            note = (f"judged unsupported by {it.get('judge')} (p={it.get('p')}) in {run.id}; "
                    f"no recheck confirmed it")
            unv.setdefault(it["claim"], {})[it["evidence"]] = note
            unverified.append({"id": eid, "claim": it["claim"], "path": it["path"], "lines": it.get("lines"),
                               "p": it.get("p"), "judge": it.get("judge"),
                               "in_recheck": any(i["id"] == eid for i in rc["items"])})
    removed, detached, dropped_ev, kept_unverified = [], [], 0, []

    def prune(cid: str, evs: list) -> list:
        nonlocal dropped_ev
        drop = bad.get(cid, {})
        dropped_ev += len(drop)
        for k, e in enumerate(evs):
            _flag(e, unv.get(cid, {}).get(k))
        return [e for k, e in enumerate(evs) if k not in drop]

    def why_removed(cid: str) -> str:
        return "no supporting evidence left: " + "; ".join(f"e{k}: {w}" for k, w in sorted(bad.get(cid, {}).items()))

    def flag_claim(cid: str, obj: dict) -> None:
        ev = obj.get("evidence") or []
        only = bool(ev) and len(unv.get(cid, {})) == len(ev)
        _flag(obj, f"every evidence item was judged unsupported without a confirming recheck ({run.id})"
              if only else None)
        if only:
            kept_unverified.append(cid)

    keep_concepts, undisputed = [], []
    for c in doc["concepts"]:
        cid = f"concept:{c['id']}"
        orig = copy.deepcopy(c)
        had = bool(c.get("evidence"))
        c["evidence"] = prune(cid, c.get("evidence", []))
        for m in c.get("maps_to", []):
            if m.get("evidence"):
                mid = f"mapping:{c['id']}->{m.get('target')}"
                m["evidence"] = prune(mid, m["evidence"])
                if not m["evidence"]:
                    m.pop("evidence")
                else:
                    flag_claim(mid, m)
        if had and not c["evidence"] and not keep_failed:
            removed.append({"claim": cid, "reason": why_removed(cid), "restore": orig})
            continue
        flag_claim(cid, c)
        if c.get("status") == "disputed" and len(c["evidence"]) < 2:
            # one reading lost its evidence: nothing is left to dispute (rule 11.4 needs both readings)
            c["status"] = "hypothesis"
            c["note"] = (c.get("note", "") + f" Dispute dropped in stage 6 ({run.id}): the evidence of one reading "
                         "did not hold.").strip()
            undisputed.append(cid)
        keep_concepts.append(c)
    gone = {r["claim"].split(":", 1)[1] for r in removed}
    doc["concepts"] = keep_concepts
    for c in doc["concepts"]:
        if "broader" in c:
            for b in c["broader"]:
                if b in gone:
                    detached.append({"concept": f"concept:{c['id']}", "broader": b,
                                     "reason": f"its broader concept:{b} was removed"})
            c["broader"] = [b for b in c["broader"] if b not in gone]
    own_verdicts: dict[str, dict[str, int]] = {}
    for it in v["items"]:
        if it["evidence"] is not None:
            x = own_verdicts.setdefault(it["claim"], {})
            x[it["verdict"]] = x.get(it["verdict"], 0) + 1
    rels = []
    for r in doc.get("relations", []):
        rid = f"relation:{r['from']}--{r['type']}--{r['to']}"
        ends = [x for x in (r["from"], r["to"]) if x in gone]
        if ends:   # removed with its end: recorded with its own evidence, so the owner can restore it
            removed.append({"claim": rid, "reason": "removed with its end " + ", ".join(f"concept:{x}" for x in ends)
                            + " (" + "; ".join(next(d["reason"] for d in removed if d["claim"] == f"concept:{x}")
                                               for x in ends) + ")",
                            "cascade_of": [f"concept:{x}" for x in ends],
                            "own_evidence": own_verdicts.get(rid, {}), "restore": copy.deepcopy(r)})
            continue
        orig = copy.deepcopy(r)
        had = bool(r.get("evidence"))
        r["evidence"] = prune(rid, r.get("evidence", []))
        if had and not r["evidence"] and not keep_failed:
            removed.append({"claim": rid, "reason": why_removed(rid), "restore": orig})
            continue
        flag_claim(rid, r)
        rels.append(r)
    doc["relations"] = rels
    var = []
    for x in doc.get("variation", []):
        vid = f"variation:{x['dimension']}--{x['value']}"
        orig = copy.deepcopy(x)
        had = bool(x.get("evidence"))
        x["evidence"] = prune(vid, x.get("evidence", []))
        if had and not x["evidence"] and not keep_failed:
            removed.append({"claim": vid, "reason": why_removed(vid), "restore": orig})
            continue
        flag_claim(vid, x)
        var.append(x)
    if "variation" in doc:
        doc["variation"] = var
    # judgement rechecks
    changed = 0
    by_id = {c["id"]: c for c in doc["concepts"]}
    for it in rc["items"]:
        r = verdicts.get(it["id"])
        if not r or r["verdict"] != "change" or it["type"] == "evidence" or origin.get(it["id"]) == "blind":
            continue
        cid = it["id"].split(":", 1)[1].split("->")[0]
        c = by_id.get(cid)
        if c is None:
            continue
        changed += 1
        note = f"Rechecked in stage 6 by {r.get('model')}: {r['reason']}"
        if it["type"] == "mapping":
            tgt = it["judgement"]["target"]
            c["maps_to"] = [m for m in c.get("maps_to", []) if m.get("target") != tgt]
            val = r.get("value")
            if val:
                c["maps_to"].append({"target": val["target"], "kind": val["kind"], "confidence": "medium",
                                     "note": note + (f" Overlap: {val['overlap']} Differs: {val['differs']}"
                                                     if val["kind"] == "partial" else "")})
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
    # the change rate counts the verdicts given in this pack: a carried one was counted in the run that gave it
    by_type: dict[str, dict] = {}
    for it in rc["items"]:
        if it["id"] not in verdicts:
            continue
        t = by_type.setdefault(it["type"], {"rechecked": 0, "changed": 0, "carried": 0})
        if origin.get(it["id"]) == "carried":
            t["carried"] += 1
            continue
        t["rechecked"] += 1
        t["changed"] += verdicts[it["id"]]["verdict"] == "change"
    mp = [it for it in rc["items"] if it["type"] == "mapping" and it["judgement"].get("kind") == "partial"
          and it["id"] in verdicts and origin.get(it["id"]) != "carried"]
    if mp:
        by_type["mapping-partial"] = {"rechecked": len(mp),
                                      "changed": sum(verdicts[it["id"]]["verdict"] == "change" for it in mp)}
    for t in by_type.values():
        t["change_rate"] = round(t["changed"] / t["rechecked"], 3) if t["rechecked"] else None
    stats = {"removed_claims": removed, "removed_with_an_end": sum(1 for r in removed if r.get("cascade_of")),
             "detached_broader": detached, "dropped_evidence": dropped_ev, "rechecks_changed": changed,
             "unverified": unverified, "kept_unverified": kept_unverified,
             "no_rechecker": blind,
             "recheck_by_type": by_type,
             "undisputed": undisputed,
             "owner": {"by": owner["by"], "verdicts": len(owner["verdicts"])} if owner else None,
             "recheck": {"model": ra.get("model"), "harness": ra.get("harness"), "run_id": ra.get("run_id"),
                         "items": len(rc["items"]), "answered": len(fresh), "carried": len(carried),
                         "unanswered_negatives": len(unanswered), "policy": policy,
                         **({"fallback_reason": ra.get("fallback_reason")} if policy == "fallback" else {})}}
    v["applied"] = {"at": now(), **stats}
    save_json(run.path("verify", f"{project}.json"), v)
    return stats
