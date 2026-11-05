"""Open Ontologies adapter (REST for graph data, MCP for engine tools).

Only per-graph operations are exposed. Whole-dataset operations (`onto_load`
with a path, `onto_plan`/`onto_apply`, `onto_clear`, `onto_rollback`) are
deliberately absent: on a shared store they replace every package at once
(see docs/open-ontologies.md).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

from ontologizer.ports.engine import Capabilities

REPO_ROOT = Path(__file__).resolve().parents[4]


class EngineError(RuntimeError):
    pass


def _token() -> str:
    tok = os.environ.get("OPEN_ONTOLOGIES_TOKEN")
    if tok:
        return tok
    out = subprocess.run(
        [str(REPO_ROOT / "scripts" / "oo"), "token"],
        capture_output=True, text=True, check=True, cwd=REPO_ROOT,
    )
    return out.stdout.strip()


def _iri(graph_iri: str) -> str:
    if not re.fullmatch(r"[^\s<>\"{}|^`\\]+", graph_iri):
        raise ValueError(f"bad IRI: {graph_iri!r}")
    return f"<{graph_iri}>"


class OpenOntologies:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 120):
        self.url = (url or os.environ.get("OPEN_ONTOLOGIES_URL") or "http://127.0.0.1:8080").rstrip("/")
        self._token = token
        self.timeout = timeout
        self._mcp_session: str | None = None
        self._rpc_id = 0

    # ---- transport -------------------------------------------------------
    @property
    def token(self) -> str:
        if self._token is None:
            self._token = _token()
        return self._token

    def _post(self, path: str, payload: dict, headers: dict | None = None):
        req = urllib.request.Request(
            f"{self.url}/{path.lstrip('/')}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _rest(self, path: str, payload: dict) -> dict | list:
        with self._post(f"api/{path}", payload) as r:
            body = json.loads(r.read() or b"null")
        if body is None:
            # v2.1.0 formats error strings into JSON unescaped; a message with a
            # quote becomes invalid JSON and the route answers `null`.
            raise EngineError(f"{path}: engine error (message lost: unescaped JSON)")
        if isinstance(body, dict) and "error" in body:
            raise EngineError(body["error"])
        return body

    def stats(self) -> dict:
        req = urllib.request.Request(f"{self.url}/api/stats",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    # ---- MCP -------------------------------------------------------------
    def _mcp_send(self, method: str, params: dict | None, notify: bool = False) -> dict | None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._rpc_id += 1
            msg["id"] = self._rpc_id
        headers = {"Accept": "application/json, text/event-stream"}
        if self._mcp_session:
            headers["Mcp-Session-Id"] = self._mcp_session
        with self._post("mcp", msg, headers) as r:
            sid = r.headers.get("mcp-session-id")
            if sid:
                self._mcp_session = sid
            raw = r.read().decode()
        if notify:
            return None
        for line in raw.splitlines():
            if line.startswith("data:") and line[5:].strip():
                data = json.loads(line[5:])
                if data.get("id") == msg["id"]:
                    if "error" in data:
                        raise EngineError(str(data["error"]))
                    return data["result"]
        if raw.strip().startswith("{"):
            return json.loads(raw).get("result")
        raise EngineError(f"no MCP response for {method}")

    def _mcp_init(self) -> None:
        if self._mcp_session:
            return
        self._mcp_send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "ontologizer", "version": "0.1"}})
        self._mcp_send("notifications/initialized", None, notify=True)

    def tool(self, name: str, **arguments) -> dict | str:
        """Call an MCP tool; returns parsed JSON text content when possible."""
        self._mcp_init()
        res = self._mcp_send("tools/call", {"name": name, "arguments": arguments})
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts)
        if res.get("isError"):
            raise EngineError(f"{name}: {text}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text

    # ---- Engine port -----------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            named_graph_write=True, graph_export=True, graph_import=True,
            shacl=True, dataset_versions=True,
            notes=("named versions snapshot the whole dataset (use graph releases instead)",
                   "onto_shacl validates the active dataset, not one graph; kit validates locally with pyshacl"),
        )

    def select(self, sparql: str) -> list[dict[str, str]]:
        body = self._rest("query", {"query": sparql})
        return body.get("results", [])

    def ask(self, sparql: str) -> bool:
        return bool(self._rest("query", {"query": sparql}).get("result"))

    def construct(self, sparql: str) -> list[tuple[str, str, str]]:
        body = self._rest("query", {"query": sparql})
        if isinstance(body, dict):
            body = body.get("results", body.get("triples", []))
        return [(t["subject"], t["predicate"], t["object"]) for t in body]

    def update(self, sparql: str) -> int:
        return int(self._rest("update", {"query": sparql}).get("affected", 0))

    def graph_names(self) -> list[str]:
        rows = self.select("SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } } ORDER BY ?g")
        return [r["g"].strip("<>") for r in rows]

    def graph_size(self, graph_iri: str) -> int:
        rows = self.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH {_iri(graph_iri)} {{ ?s ?p ?o }} }}")
        return int(rows[0]["n"].split("^^")[0].strip('"')) if rows else 0

    def export_graph(self, graph_iri: str) -> str:
        triples = self.construct(
            f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH {_iri(graph_iri)} {{ ?s ?p ?o }} }}")
        lines = sorted({f"{s} {p} {o} ." for s, p, o in triples})
        return "\n".join(lines) + ("\n" if lines else "")

    def snapshot(self, prefix: str = "") -> dict[str, str]:
        """All named graphs (optionally IRI-prefixed) in ONE query → {graph: N-Triples}.
        One request is one consistent read of all graphs."""
        flt = f'FILTER(STRSTARTS(STR(?g), "{prefix}"))' if prefix else ""
        rows = self.select(f"SELECT ?g ?s ?p ?o WHERE {{ GRAPH ?g {{ ?s ?p ?o }} {flt} }}")
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["g"].strip("<>"), []).append(f"{r['s']} {r['p']} {r['o']} .")
        return {g: "\n".join(sorted(set(v))) + "\n" for g, v in out.items()}

    def replace_graph(self, graph_iri: str, ntriples: str) -> int:
        g = _iri(graph_iri)
        # One update request = one transaction in Oxigraph: readers never see the empty graph.
        self.update(f"DROP SILENT GRAPH {g} ;\nINSERT DATA {{ GRAPH {g} {{\n{ntriples}\n}} }}")
        return self.graph_size(graph_iri)

    def add_to_graph(self, graph_iri: str, ntriples: str) -> int:
        if not ntriples.strip():
            return 0
        return self.update(f"INSERT DATA {{ GRAPH {_iri(graph_iri)} {{\n{ntriples}\n}} }}")

    def remove_from_graph(self, graph_iri: str, ntriples: str) -> int:
        if not ntriples.strip():
            return 0
        return self.update(f"DELETE DATA {{ GRAPH {_iri(graph_iri)} {{\n{ntriples}\n}} }}")

    def drop_graph(self, graph_iri: str) -> None:
        self.update(f"DROP SILENT GRAPH {_iri(graph_iri)}")
