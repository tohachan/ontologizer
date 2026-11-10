"""Open Ontologies adapter (REST for graph data, MCP for engine tools).

Only per-graph operations are exposed. Whole-dataset operations (`onto_load`
with a path, `onto_plan`/`onto_apply`, `onto_clear`, `onto_rollback`) are
deliberately absent: on a shared store they replace every package at once
(see docs/open-ontologies.md).
"""

from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from ontologizer.ports.engine import Capabilities, EngineError, EngineUnavailable, ResponseTooLarge

REPO_ROOT = Path(__file__).resolve().parents[4]
# The server refuses request bodies above about 2 MiB (HTTP 413); a graph write larger than this many
# bytes of SPARQL is split. `OPEN_ONTOLOGIES_MAX_UPDATE_BYTES` overrides it.
MAX_UPDATE_BYTES = 1_500_000
STAGING_PREFIX = "urn:ontologizer-staging:"   # outside the kit's graph prefix, so snapshots never see it


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
    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 120,
                 max_update_bytes: int | None = None):
        self.url = (url or os.environ.get("OPEN_ONTOLOGIES_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.max_update_bytes = int(max_update_bytes or os.environ.get("OPEN_ONTOLOGIES_MAX_UPDATE_BYTES")
                                    or MAX_UPDATE_BYTES)
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

    def _post(self, path: str, payload: dict, headers: dict | None = None, timeout: float | None = None):
        req = urllib.request.Request(
            f"{self.url}/{path.lstrip('/')}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        return urllib.request.urlopen(req, timeout=self.timeout if timeout is None else timeout)

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

    def select_bounded(self, sparql: str, max_bytes: int, timeout: float) -> list[dict[str, str]]:
        """SELECT for a reader that must not buffer without limit or wait without end. Every failure
        raises, so none of them can look like an empty result."""
        try:
            raw = self._read_bounded({"query": sparql}, max_bytes, timeout)
        except urllib.error.HTTPError as e:
            raise EngineError(f"query: HTTP {e.code}") from None
        except (OSError, http.client.HTTPException, subprocess.SubprocessError) as e:
            raise EngineUnavailable(f"query: no answer from {self.url}: {type(e).__name__}") from None
        try:
            body = json.loads(raw)
        except ValueError:
            raise EngineError("query: the answer is not JSON") from None
        if body is None:
            raise EngineError("query: engine error (message lost: unescaped JSON)")   # see _rest
        if isinstance(body, dict) and "error" in body:
            raise EngineError(str(body["error"]))
        rows = body.get("results") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise EngineError("query: the answer has no results list")
        return rows

    def _read_bounded(self, payload: dict, max_bytes: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        with self._post("api/query", payload, timeout=timeout) as r:
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge(f"query: the answer declares {declared} bytes, the limit is {max_bytes}")
            chunks, total = [], 0
            while chunk := r.read1(65536):   # read1 returns after one receive, so the deadline is checked
                total += len(chunk)
                if total > max_bytes:
                    raise ResponseTooLarge(f"query: the answer exceeds {max_bytes} bytes")
                if time.monotonic() > deadline:
                    raise TimeoutError
                chunks.append(chunk)
        return b"".join(chunks)

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

    def _chunks(self, ntriples: str, overhead: int) -> list[str]:
        """N-Triples split at line ends so that each chunk plus `overhead` bytes of SPARQL fits one request."""
        room = max(self.max_update_bytes - overhead, 1)
        out, cur, size = [], [], 0
        for line in ntriples.splitlines(keepends=True):
            n = len(line.encode())
            if cur and size + n > room:
                out.append("".join(cur))
                cur, size = [], 0
            cur.append(line)
            size += n
        if cur:
            out.append("".join(cur))
        return out

    def replace_graph(self, graph_iri: str, ntriples: str) -> int:
        g = _iri(graph_iri)
        one = f"DROP SILENT GRAPH {g} ;\nINSERT DATA {{ GRAPH {g} {{\n{ntriples}\n}} }}"
        if len(one.encode()) <= self.max_update_bytes:
            # One update request = one transaction in Oxigraph: readers never see the empty graph.
            self.update(one)
            return self.graph_size(graph_iri)
        # Too large for one request: write a staging graph in chunks, then swap it in with one MOVE,
        # which is again a single transaction, so readers see the old graph or the new one, never a part.
        st = _iri(STAGING_PREFIX + uuid.uuid4().hex)
        head = f"INSERT DATA {{ GRAPH {st} {{\n"
        try:
            self.update(f"DROP SILENT GRAPH {st}")
            for chunk in self._chunks(ntriples, len(head.encode()) + 8):
                self.update(f"{head}{chunk}\n}} }}")
            self.update(f"MOVE SILENT GRAPH {st} TO {g}")
        except Exception:
            try:
                self.update(f"DROP SILENT GRAPH {st}")
            finally:
                raise
        return self.graph_size(graph_iri)

    def _data_update(self, verb: str, graph_iri: str, ntriples: str) -> int:
        if not ntriples.strip():
            return 0
        head = f"{verb} DATA {{ GRAPH {_iri(graph_iri)} {{\n"
        return sum(self.update(f"{head}{chunk}\n}} }}") for chunk in self._chunks(ntriples, len(head.encode()) + 8))

    def add_to_graph(self, graph_iri: str, ntriples: str) -> int:
        return self._data_update("INSERT", graph_iri, ntriples)

    def remove_from_graph(self, graph_iri: str, ntriples: str) -> int:
        return self._data_update("DELETE", graph_iri, ntriples)

    def drop_graph(self, graph_iri: str) -> None:
        self.update(f"DROP SILENT GRAPH {_iri(graph_iri)}")
