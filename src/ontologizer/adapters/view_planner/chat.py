"""A view planner over an OpenAI-compatible chat-completions endpoint (standard library only).

Config, in the hub's `visualizer.planner` (or its `ontologizer.local.yaml`):

    visualizer:
      planner:
        adapter: chat
        url: https://openrouter.ai/api/v1      # any OpenAI-compatible endpoint
        model: <provider/model>               # required: no model is assumed
        env: OPENROUTER_API_KEY               # the environment, else the hub's .env (core/envfile.py)
        timeout: 60

The key is sent only to `url` and named in no error. The model is asked for one JSON object, either
{"spec": …, "summary": …} or {"candidates": [{"name": …, "iris": […]}]}; the kit checks it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from ontologizer.core.envfile import read_key
from ontologizer.ports.view_planner import CONTRACT_VERSION, PlannerError, PlanRequest, PlanResult

INSTRUCTIONS = """You plan a diagram of part of an ontology. You get a request in any language, the
current view specification (JSON), and a catalog of the concepts and relationships the view may use.
Answer with exactly one JSON object and nothing else:
- {"spec": <a complete view specification>, "summary": "<one sentence: what changed>"}, or
- {"candidates": [{"name": "<a name from the request>", "iris": ["<catalog IRI>", ...]}]} when a name in
  the request matches several concepts and you cannot tell which is meant.
Rules: keep schemaVersion, id, title and scope exactly as in the current specification. Use only IRIs
and relationship IDs from the catalog. Match the request to concepts by meaning: labels may be in
another language than the request. Do not invent relationships; a summary is a path of 2 to 6
relationship IDs that connect in the stated directions. Hops are 0 to 3. Annotations are plain text.
presentation.arrangement, optional, groups the cards on the canvas, outer key first: lanes by the first
key, boxes inside a lane by the second, from "level", "graph", "category" (the gUFO category) and
"group"; [] means no grouping, and leaving it out keeps level lanes."""


class ChatPlanner:
    def __init__(self, conf: dict, base: Path):
        if not conf.get("model"):
            raise ValueError("visualizer.planner: `model` is required")
        self.url = str(conf.get("url", "https://openrouter.ai/api/v1")).rstrip("/")
        self.model = str(conf["model"])
        self.timeout = float(conf.get("timeout", 60))
        self.env = str(conf.get("env", "OPENROUTER_API_KEY"))
        self._key = read_key(self.env, [Path(base) / ".env"])   # a worktree falls back to the main checkout's .env

    def plan(self, request: PlanRequest) -> PlanResult:
        if request.contract_version != CONTRACT_VERSION:
            raise PlannerError(f"contract version {request.contract_version} is not supported")
        if not self._key:
            raise PlannerError(f"no API key: set {self.env}")
        payload = {k: v for k, v in asdict(request).items() if k != "contract_version"}
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": INSTRUCTIONS},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        req = urllib.request.Request(f"{self.url}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise PlannerError(f"the provider answered HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise PlannerError(f"the provider did not answer usably: {type(e).__name__}") from None
        try:
            text = data["choices"][0]["message"]["content"]
            out = json.loads(text[text.index("{"): text.rindex("}") + 1])
        except (KeyError, IndexError, TypeError, ValueError):
            raise PlannerError("the provider's answer is not one JSON object") from None
        if isinstance(out.get("candidates"), list) and not out.get("spec"):
            return PlanResult(CONTRACT_VERSION, None, "", tuple(c for c in out["candidates"] if isinstance(c, dict)))
        return PlanResult(CONTRACT_VERSION, out.get("spec"), str(out.get("summary", ""))[:500])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # the key never follows a redirect
        return None


def factory(conf: dict, *, base: Path) -> ChatPlanner:
    return ChatPlanner(conf, base)
