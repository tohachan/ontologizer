"""A run of the analysis process (docs/process.md): its directory, its decision log,
and the decision switch `jev | llm | off` of every stage.

The owner config carries:

    process:
      runs: runs                 # run directories, relative to the config
      run: r1                    # default run id (`--run` overrides)
      frame: frame.md
      max_concepts: null         # optional cap for pilots
      extractors: [{id: opus, harness: claude, model: claude-opus-5-5}, …]
    decisions:
      adapter: jev               # adapter used in `jev` mode
      default: off               # mode of a stage not listed below
      stages: {inventory: jev, reconcile: jev, map: jev, category: jev, group: jev, verify: jev}
      thresholds: {same_concept: 0.5, …}   # provisional until `gold calibrate`
      runs: {r2-nojev: {stages: {reconcile: off}}}   # per-run overrides (ablation)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import yaml

from ontologizer.core import plugins
from ontologizer.core.config import Config
from ontologizer.ports.decisions import Answer, Question, key

MODES = ("jev", "llm", "off")
BATCH = 20   # questions per adapter request

DEFAULT_THRESHOLDS = {
    "triage_keep": 0.5,       # inventory: P(architectural) to count a candidate
    "same_concept": 0.5,      # reconcile: P(same) to accept a pair
    "group_same_concept": None,  # group (stage 5): P(same) to link two concepts; None → same_concept
    "compare_same_concept": None,  # release-compare: P(same) across releases; None → group_same_concept
    "map_candidate": 0.2,     # map: P(option) to propose a target to the LLM
    "category_agree": 0.5,    # map: P(extraction's category) below which the category is disputed
    "support": 0.5,           # verify: P(lines support the claim)
    "recheck_confidence": 0.6,  # verify: answers below this confidence are rechecked by another model
}

DEFAULT_EXTRACTORS = [
    {"id": "opus", "harness": "claude", "model": "claude-opus-5-5"},
    {"id": "sonnet", "harness": "claude", "model": "claude-sonnet-5-5"},
]


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def load_json(p: Path, default=None):
    return json.loads(p.read_text()) if p.is_file() else default


def save_json(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n")
    return p


class PendingDecisions(Exception):
    """llm mode: questions were written for an agent; re-run the stage after it answers."""

    def __init__(self, stage: str, count: int, path: Path):
        super().__init__(f"{stage}: {count} question(s) wait for an LLM agent in {path}; "
                         f"answer them in {path.with_name(path.name.replace('.pending', '.answers'))} "
                         f"(skill analyse-corpus, §Answering decisions) and re-run")
        self.stage, self.count, self.path = stage, count, path


class Run:
    def __init__(self, cfg: Config, run_id: str | None = None):
        self.cfg = cfg
        proc = cfg.data.get("process") or {}
        self.id = run_id or proc.get("run")
        if not self.id:
            raise ValueError("no run id: pass --run or set `process.run` in the config")
        self.root = cfg.root / proc.get("runs", "runs") / self.id
        self.process = proc
        self.decisions_conf = dict(cfg.data.get("decisions") or {})
        # per-run overrides, e.g. an ablation run with some stages off:
        #   decisions: {runs: {r2-nojev: {stages: {inventory: off, reconcile: off}}}}
        over = (self.decisions_conf.pop("runs", None) or {}).get(self.id) or {}
        for k, v in over.items():
            if isinstance(v, dict) and isinstance(self.decisions_conf.get(k), dict):
                self.decisions_conf[k] = {**self.decisions_conf[k], **v}
            else:
                self.decisions_conf[k] = v
        self._adapter = None
        self._cache: dict[tuple, dict] | None = None
        self._state_hashes: set[str] | None = None

    # ---- files ---------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def meta(self) -> dict:
        return yaml.safe_load(self.path("run.yaml").read_text()) if self.path("run.yaml").is_file() else {}

    def update_meta(self, **kv) -> None:
        m = self.meta() or {"run": self.id, "started": now()}
        for k, v in kv.items():
            if isinstance(v, dict) and isinstance(m.get(k), dict):
                m[k] = {**m[k], **v}
            else:
                m[k] = v
        self.root.mkdir(parents=True, exist_ok=True)
        self.path("run.yaml").write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True))

    @property
    def extractors(self) -> list[dict]:
        return list(self.process.get("extractors") or DEFAULT_EXTRACTORS)

    @property
    def overlay(self) -> bool:
        """Stage 9: this run maps the frame's overlay target onto a released shared level
        (`process.overlay: true` in the target owner's config); nothing is consolidated from it."""
        return bool(self.process.get("overlay"))

    @property
    def max_concepts(self) -> int | None:
        return self.process.get("max_concepts")

    # ---- decisions -------------------------------------------------------------
    def mode(self, stage: str) -> str:
        m = (self.decisions_conf.get("stages") or {}).get(stage, self.decisions_conf.get("default", "off"))
        if m is False:   # YAML 1.1 reads a bare `off` as false
            m = "off"
        if m not in MODES:
            raise ValueError(f"decisions.stages.{stage}: one of {MODES}, got {m!r}")
        return m

    def threshold(self, name: str) -> float:
        c = self.calibration()
        # a provisional threshold (fewer than 20 gold items) is reported, not applied, unless the config says so;
        # `provisional` is the list of such thresholds (True: the whole calibration, older files)
        prov = c.get("provisional")
        cal = dict(c.get("thresholds", {}))
        if not self.process.get("apply_provisional"):
            if prov is True:
                cal = {}
            elif isinstance(prov, list):
                cal = {k: v for k, v in cal.items() if k not in prov}
        conf = self.decisions_conf.get("thresholds") or {}
        v = cal.get(name, conf.get(name, DEFAULT_THRESHOLDS[name]))
        if v is None and name == "group_same_concept":
            return self.threshold("same_concept")
        if v is None and name == "compare_same_concept":
            return self.threshold("group_same_concept")
        return float(v)

    def calibration(self) -> dict:
        p = self.cfg.root / (self.process.get("calibration") or "calibration.yaml")
        return (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}

    def adapter(self):
        if self._adapter is None:
            conf = {k: v for k, v in self.decisions_conf.items() if k not in ("default", "stages", "thresholds")}
            conf.setdefault("adapter", "jev")
            self._adapter = plugins.load("decisions", conf, base=self.cfg.root)
        return self._adapter

    def set_adapter(self, adapter) -> None:
        """Tests and callers that already hold an adapter."""
        self._adapter = adapter

    def states(self) -> dict[str, object]:
        """Decision states by hash: a state shared by a batch of questions is stored once."""
        p = self.path("decision-states.jsonl")
        if not p.is_file():
            return {}
        out = {}
        for line in p.read_text().splitlines():
            if line.strip():
                x = json.loads(line)
                out[x["hash"]] = x["state"]
        return out

    def log_entries(self, with_state: bool = True) -> list[dict]:
        p = self.path("decisions.jsonl")
        if not p.is_file():
            return []
        entries = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        if with_state and any("state_hash" in e for e in entries):
            st = self.states()
            for e in entries:
                if "state_hash" in e and "state" not in e:
                    e["state"] = st.get(e["state_hash"])
        return entries

    def _log(self, entry: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = dict(entry)
        if "state" in entry:
            state = entry.pop("state")
            h = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            entry["state_hash"] = h
            if self._state_hashes is None:
                self._state_hashes = set(self.states())
            if h not in self._state_hashes:
                with self.path("decision-states.jsonl").open("a") as f:
                    f.write(json.dumps({"hash": h, "state": state}, ensure_ascii=False, sort_keys=True) + "\n")
                self._state_hashes.add(h)
        with self.path("decisions.jsonl").open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        if self._cache is not None and not (entry.get("answer") or {}).get("error"):
            self._cache[(entry["key"], entry["mode"], entry.get("model"))] = entry

    def _cached(self, k: str, mode: str, model: str | None) -> dict | None:
        """A logged answer to reuse: same question, mode and model; errors are never reused (retried)."""
        if self._cache is None:
            self._cache = {(e["key"], e["mode"], e.get("model")): e for e in self.log_entries(with_state=False)
                           if not (e.get("answer") or {}).get("error")}
        return self._cache.get((k, mode, model))

    def role_model(self, stage: str) -> str | None:
        """`process.roles.<stage>`: the model that must answer a stage's LLM decisions
        (e.g. `map: claude-opus-5-5`, `inventory: gpt-6-astra` for llm-mode triage)."""
        return (self.process.get("roles") or {}).get(stage)

    def decider(self, stage: str, mode: str | None = None) -> "Decider":
        return Decider(self, stage, mode or self.mode(stage))


class Decider:
    """Asks the narrow questions of one stage in that stage's mode and logs every answer.

    `ask` returns the answers it has. In `llm` mode the missing ones are queued
    in `decisions/<stage>.pending.json`; call `done()` after the stage's loop: it
    raises PendingDecisions when anything is still unanswered.
    """

    def __init__(self, run: Run, stage: str, mode: str):
        self.run, self.stage, self.mode = run, stage, mode
        self.pending: list[dict] = []
        self.asked = 0

    @property
    def on(self) -> bool:
        return self.mode != "off"

    def _answers_file(self) -> dict:
        return load_json(self.run.path("decisions", f"{self.stage}.answers.json"), {}) or {}

    def ask(self, state: str | dict, questions: list[Question]) -> dict[str, Answer]:
        if self.mode == "off" or not questions:
            return {}
        out: dict[str, Answer] = {}
        todo: list[tuple[str, Question]] = []
        model = getattr(self.run.adapter(), "model", None) if self.mode == "jev" else \
            self._answers_file().get("model")
        for q in questions:
            k = key(self.stage, state, q)
            hit = self.run._cached(k, self.mode, model)
            if hit:
                out[q.id] = Answer.from_dict({**hit["answer"], "id": q.id})
            else:
                todo.append((k, q))
        if not todo:
            return out
        self.asked += len(todo)
        if self.mode == "jev":
            ad = self.run.adapter()
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                answers, receipt = ad.decide(state, [q for _, q in chunk])
                by_id = {a.id: a for a in answers}
                for k, q in chunk:
                    a = by_id.get(q.id) or Answer(q.id, q.type, None, error="missing")
                    out[q.id] = a
                    self.run._log({"at": now(), "stage": self.stage, "mode": "jev", "key": k,
                                   "model": getattr(ad, "model", None), "state": state,
                                   "question": q.to_dict(), "answer": a.to_dict(),
                                   "receipt": {**receipt.__dict__, "batch": len(chunk),
                                               "cost_share": None if receipt.cost is None
                                               else receipt.cost / len(chunk)}})
            return out
        # llm: answers written by an agent, keyed by question key
        answers = self._answers_file()
        if answers.get("answers") and not model:
            raise ValueError(f"{self.run.path('decisions', self.stage + '.answers.json')}: `model` required")
        need = self.run.role_model(self.stage)
        if answers.get("answers") and need and model != need:
            raise ValueError(f"{self.run.path('decisions', self.stage + '.answers.json')}: answered by {model}; "
                             f"process.roles.{self.stage} requires {need}")
        for k, q in todo:
            a = (answers.get("answers") or {}).get(k)
            if a is None:
                self.pending.append({"key": k, "state": state, "question": q.to_dict()})
                continue
            ans = _llm_answer(q, a)
            out[q.id] = ans
            self.run._log({"at": now(), "stage": self.stage, "mode": "llm", "key": k, "model": model,
                           "state": state, "question": q.to_dict(), "answer": ans.to_dict(),
                           "reason": a.get("reason")})
        return out

    def done(self) -> None:
        p = self.run.path("decisions", f"{self.stage}.pending.json")
        if not self.pending:
            if p.is_file():
                p.unlink()
            return
        save_json(p, {"stage": self.stage, "instructions": PENDING_HELP, "questions": self.pending})
        raise PendingDecisions(self.stage, len(self.pending), p)


PENDING_HELP = (
    "Answer every question about its `state`. Write `<stage>.answers.json` next to this file as "
    "{\"model\": \"<your model id>\", \"answers\": {\"<key>\": {\"value\": …, \"confidence\": 0..1, "
    "\"reason\": \"…\"}}}. choice: value is one option name; score: value is the 0-based level index; "
    "noul: value is your probability (0..1) that the statement is true. Never invent an answer you "
    "cannot give: use {\"error\": \"refused\"}.")


def _llm_answer(q: Question, a: dict) -> Answer:
    if a.get("error"):
        return Answer(q.id, q.type, None, error=str(a["error"]))
    v = a.get("value")
    if q.type == "choice" and v not in (q.options or {}):
        return Answer(q.id, q.type, None, error=f"invalid option {v!r}")
    if q.type == "score":
        try:
            v = int(v)
        except (TypeError, ValueError):
            return Answer(q.id, q.type, None, error=f"invalid level {v!r}")
        if not 0 <= v < len(q.levels or ()):
            return Answer(q.id, q.type, None, error=f"invalid level {v!r}")
    if q.type == "noul":
        if isinstance(v, bool):
            v = 1.0 if v else 0.0
        try:
            v = float(v)
        except (TypeError, ValueError):
            return Answer(q.id, q.type, None, error=f"invalid probability {v!r}")
    conf = a.get("confidence")
    probs = a.get("probabilities") or ({str(v): float(conf)} if conf is not None and q.type != "noul" else {})
    return Answer(q.id, q.type, v, probabilities=probs, confidence=None if conf is None else float(conf))
