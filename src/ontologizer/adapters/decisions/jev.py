"""Jev decision adapter (built in): the kit's decision port over OpenRouter's Decisions API.

Used only when a config names `decisions: {adapter: jev}`; it needs an OpenRouter key, no extra package.

Config: `decisions: {adapter: jev, model: typesafe/jev-1.13, env: OPENROUTER_API_KEY, dotenv: .env}`.
The key is read from the environment, else from the `.env` named by `dotenv`
(relative to the owner config, then the kit's checkout). It is never printed,
logged or put into an error message.

Wire format (OpenRouter Decisions, alpha): POST {base}/decisions with
{model, state, questions: {id: {type: noul|choice|score, instructions, criteria}}};
answers {id: {type, noul | choice+probabilities | score+probabilities, confidence}}.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ontologizer.ports.decisions import Answer, Question, Receipt

BASE = "https://openrouter.ai/api/alpha"


def _read_key(env: str, dotenv: list[Path]) -> str | None:
    if os.environ.get(env):
        return os.environ[env]
    for p in dotenv:
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith(f"{env}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def wire_question(q: Question) -> dict:
    if q.type == "noul":
        d = {"type": "noul", "instructions": q.instructions}
        if q.true is not None:
            d["criteria"] = {"true": q.true, "false": q.false}
        return d
    if q.type == "choice":
        return {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
    return {"type": "score", "instructions": q.instructions, "criteria": list(q.levels)}


def parse_answer(q: Question, raw: dict | None) -> Answer:
    if not isinstance(raw, dict):
        return Answer(q.id, q.type, None, error="missing")
    if raw.get("type") == "refusal" or raw.get("type") not in (None, q.type):
        return Answer(q.id, q.type, None, error=str(raw.get("type")))
    conf = raw.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) and 0 <= conf <= 1 else None
    probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
    if q.type == "noul":
        v = raw.get("noul")
        if not isinstance(v, (int, float)) or not 0 <= v <= 1:
            return Answer(q.id, q.type, None, error="invalid")
        return Answer(q.id, q.type, float(v), confidence=conf)
    if q.type == "choice":
        v = raw.get("choice")
        ok = v in (q.options or {})
        return Answer(q.id, q.type, v if ok else None, probabilities=probs, confidence=conf,
                      error=None if ok else "invalid")
    v = raw.get("score")   # the API reports the expected level (e.g. 1.61); take the most probable one
    if probs:
        v = max(probs, key=lambda k: probs[k])
    try:
        v = int(round(float(v)))
    except (TypeError, ValueError):
        return Answer(q.id, q.type, None, error="invalid")
    if not 0 <= v < len(q.levels or ()):
        return Answer(q.id, q.type, None, error="invalid")
    return Answer(q.id, q.type, v, probabilities=probs, confidence=conf)


class Jev:
    name = "jev"

    def __init__(self, conf: dict, *, base: Path):
        self.model = conf.get("model", "typesafe/jev-1.13")
        self.base_url = conf.get("url", BASE).rstrip("/")
        self.timeout = float(conf.get("timeout", 60))
        self.retries = int(conf.get("retries", 2))
        env = conf.get("env", "OPENROUTER_API_KEY")
        import ontologizer
        kit = Path(ontologizer.__file__).resolve().parents[2]   # the kit checkout (src layout)
        dotenv = [base / conf["dotenv"]] if conf.get("dotenv") else []
        dotenv += [base / ".env", kit / ".env"]
        self._key = _read_key(env, dotenv)
        self._env = env

    def decide(self, state, questions: list[Question]) -> tuple[list[Answer], Receipt]:
        if not self._key:
            raise RuntimeError(f"jev: no API key: set {self._env} in the environment or .env")
        body = {"model": self.model, "state": state,
                "questions": {q.id: wire_question(q) for q in questions}}
        req = urllib.request.Request(f"{self.base_url}/decisions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self._key}",
                                              "Content-Type": "application/json"}, method="POST")
        last = ""
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"   # body omitted: it may echo inputs
                if e.code in (400, 401, 403, 404, 422) or attempt == self.retries:
                    return [Answer(q.id, q.type, None, error=last) for q in questions], Receipt(self.model)
            except (urllib.error.URLError, TimeoutError) as e:
                last = type(e).__name__
                if attempt == self.retries:
                    return [Answer(q.id, q.type, None, error=last) for q in questions], Receipt(self.model)
            time.sleep(2 ** attempt)
        answers = data.get("answers") or {}
        usage = data.get("usage") or {}
        receipt = Receipt(model=data.get("model", self.model), input_tokens=usage.get("input_tokens"),
                          output_tokens=usage.get("output_tokens"), cost=usage.get("cost"),
                          request_id=data.get("id"))
        return [parse_answer(q, answers.get(q.id)) for q in questions], receipt


def factory(conf: dict, *, base: Path) -> Jev:
    return Jev(conf, base=base)
