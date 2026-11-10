"""Bounded read of selected graphs. Hermetic: a local HTTP server stands in for the engine's
transport, a stub for its answers. Nothing here reaches a store."""

from __future__ import annotations

import http.server
import json
import threading
import time

import pytest
from rdflib import BNode, Literal, URIRef

from ontologizer.adapters.engine.open_ontologies import EngineError as AdapterEngineError
from ontologizer.adapters.engine.open_ontologies import OpenOntologies
from ontologizer.core.views.source import Limits, ScopedReader, ScopeTooLarge
from ontologizer.ports.engine import EngineError, EngineUnavailable, ResponseTooLarge

A, B = "urn:sample:graph:a", "urn:sample:graph:b"
TOKEN = "secret-token-value"


# ---- transport -------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.seen.append(self.headers.get("Authorization"))
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.respond(self)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    s.daemon_threads = True
    s.seen = []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def _store(server):
    return OpenOntologies(url=f"http://127.0.0.1:{server.server_port}", token=TOKEN)


def _answer(body: bytes, status=200, length=True):
    def respond(h):
        h.send_response(status)
        if length:
            h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
    return respond


def test_bounded_select_returns_rows(server):
    server.respond = _answer(json.dumps({"variables": ["x"], "results": [{"x": "<urn:a>"}]}).encode())
    assert _store(server).select_bounded("SELECT", max_bytes=1000, timeout=5) == [{"x": "<urn:a>"}]
    assert server.seen == [f"Bearer {TOKEN}"]


@pytest.mark.parametrize("length", [True, False], ids=["declared", "undeclared"])
def test_bounded_select_stops_at_the_byte_limit(server, length):
    server.respond = _answer(b"x" * 100_000, length=length)
    with pytest.raises(ResponseTooLarge):
        _store(server).select_bounded("SELECT", max_bytes=1000, timeout=5)


@pytest.mark.parametrize("body", [b"null", b"{broken", b'{"variables": []}', b'{"results": null}', b"[]"],
                         ids=["null", "broken", "no-results", "null-results", "list"])
def test_bounded_select_refuses_an_unusable_answer(server, body):
    server.respond = _answer(body)
    with pytest.raises(EngineError):
        _store(server).select_bounded("SELECT", max_bytes=1000, timeout=5)


def test_bounded_select_reports_the_engines_error(server):
    server.respond = _answer(b'{"error": "bad query"}')
    with pytest.raises(EngineError, match="bad query"):
        _store(server).select_bounded("SELECT", max_bytes=1000, timeout=5)


def test_bounded_select_http_error_does_not_leak_the_token(server):
    server.respond = _answer(b"nope", status=500)
    with pytest.raises(EngineError) as e:
        _store(server).select_bounded("SELECT", max_bytes=1000, timeout=5)
    assert "500" in str(e.value) and TOKEN not in str(e.value)
    assert not isinstance(e.value, EngineUnavailable)


def test_bounded_select_silent_server_times_out(server):
    release = threading.Event()
    server.respond = lambda h: release.wait(5)
    t0 = time.monotonic()
    with pytest.raises(EngineUnavailable):
        _store(server).select_bounded("SELECT", max_bytes=1000, timeout=0.2)
    release.set()
    assert time.monotonic() - t0 < 3


def test_bounded_select_dripping_server_hits_the_deadline(server):
    release = threading.Event()

    def drip(h):
        h.send_response(200)
        h.end_headers()
        while not release.wait(0.05):
            try:
                h.wfile.write(b" ")
                h.wfile.flush()
            except OSError:
                return

    server.respond = drip
    t0 = time.monotonic()
    with pytest.raises(EngineUnavailable):
        _store(server).select_bounded("SELECT", max_bytes=1_000_000, timeout=0.3)
    release.set()
    assert time.monotonic() - t0 < 3


def test_bounded_select_no_server_is_unavailable():
    s = OpenOntologies(url="http://127.0.0.1:9", token=TOKEN)
    with pytest.raises(EngineUnavailable):
        s.select_bounded("SELECT", max_bytes=1000, timeout=1)


def test_engine_error_is_one_type_for_the_kit():
    assert AdapterEngineError is EngineError


# ---- the facade ------------------------------------------------------------------
class Stub:
    """Only the one method the facade may use; anything else would raise AttributeError."""

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    def select_bounded(self, sparql, max_bytes, timeout):
        self.calls.append((sparql, max_bytes, timeout))
        if self.error:
            raise self.error
        return self.rows


def row(g, s, p, o):
    return {"g": f"<{g}>", "s": s, "p": p, "o": o}


P = "<urn:sample:p>"


def test_read_asks_for_exactly_the_selected_graphs():
    e = Stub()
    ScopedReader(e, Limits(max_quads=10, max_bytes=500, timeout=7)).read([B, A, A])
    sparql, max_bytes, timeout = e.calls[0]
    assert f"VALUES ?g {{ <{A}> <{B}> }}" in sparql and "GRAPH ?g" in sparql
    assert "LIMIT 11" in sparql and "FILTER" not in sparql
    assert (max_bytes, timeout) == (500, 7)


@pytest.mark.parametrize("graphs", [[], ["urn:a> } } DROP ALL #"], ["with space"]], ids=["none", "injection", "space"])
def test_read_refuses_a_bad_scope_before_any_query(graphs):
    e = Stub()
    with pytest.raises(ValueError):
        ScopedReader(e).read(graphs)
    assert e.calls == []


def test_read_overflow_by_rows_is_an_error_not_a_truncation():
    rows = [row(A, f"<urn:s{i}>", P, '"x"') for i in range(4)]
    with pytest.raises(ScopeTooLarge) as e:
        ScopedReader(Stub(rows), Limits(max_quads=3)).read([A])
    assert e.value.limit == "quads" and e.value.ceiling == 3


def test_read_at_the_ceiling_is_fine():
    rows = [row(A, f"<urn:s{i}>", P, '"x"') for i in range(3)]
    assert ScopedReader(Stub(rows), Limits(max_quads=3)).read([A]).quad_count == 3


def test_read_overflow_by_bytes_is_an_error():
    with pytest.raises(ScopeTooLarge) as e:
        ScopedReader(Stub(error=ResponseTooLarge("big")), Limits(max_bytes=9)).read([A])
    assert e.value.limit == "bytes" and e.value.ceiling == 9


def test_read_engine_failure_is_not_an_empty_snapshot():
    with pytest.raises(EngineUnavailable):
        ScopedReader(Stub(error=EngineUnavailable("down"))).read([A])


@pytest.mark.parametrize("bad", [
    {"g": f"<{A}>", "s": "<urn:s>", "p": P},
    {"g": f"<{A}>", "s": "<urn:s>", "p": P, "o": None},
    "not a row",
    row(A, "not a term", P, '"x"'),
    row(A, "<urn:s>", '"literal predicate"', '"x"'),
    row(A, "<urn:s>", P, "not a term"),
    row(A, "<urn:s>", P, '"unterminated'),
    row(A, "<urn:s>", P, f'"x" <{B}> .\n<urn:other> {P} "y"'),
], ids=["missing-field", "null-term", "not-a-row", "bad-subject", "bad-predicate", "bad-object",
        "unterminated-literal", "object-smuggles-a-quad"])
def test_read_malformed_row_is_an_upstream_error(bad):
    with pytest.raises(EngineError):
        ScopedReader(Stub([bad])).read([A])


def test_read_refuses_a_row_from_an_unselected_graph():
    with pytest.raises(EngineError, match="unselected"):
        ScopedReader(Stub([row(B, "<urn:s>", P, '"x"')])).read([A])


def test_snapshot_keeps_graph_membership_and_reports_missing_graphs():
    snap = ScopedReader(Stub([row(A, "<urn:s>", P, "<urn:o>")])).read([A, B])
    assert snap.graphs == (A, B) and snap.missing == (B,)
    assert set(snap.fingerprints) == {A}
    assert (URIRef("urn:s"), URIRef("urn:sample:p"), URIRef("urn:o")) in snap.graph(A)
    assert len(snap.graph(B)) == 0


def test_snapshot_round_trips_literals():
    rows = [row(A, "<urn:s>", P, o) for o in (
        '"say \\"hi\\"\\nnext"@en', '"\\u043F\\u0440\\u0438\\u0432\\u0435\\u0442"@ru', '"Привет"@ru',
        '"5"^^<http://www.w3.org/2001/XMLSchema#integer>', '"plain"')]
    objs = set(ScopedReader(Stub(rows)).read([A]).graph(A).objects())
    assert Literal('say "hi"\nnext', lang="en") in objs
    assert Literal("Привет", lang="ru") in objs
    assert Literal(5) in objs and Literal("plain") in objs


def test_blank_node_keeps_one_identity_across_graphs():
    rows = [row(A, "<urn:s>", P, "_:shared"), row(B, "_:shared", P, '"x"'), row(B, "<urn:t>", P, "_:other")]
    snap = ScopedReader(Stub(rows)).read([A, B])
    in_a = next(iter(snap.graph(A).objects()))
    assert isinstance(in_a, BNode)
    assert (in_a, URIRef("urn:sample:p"), Literal("x")) in snap.graph(B)
    assert (URIRef("urn:t"), URIRef("urn:sample:p"), in_a) not in snap.graph(B)


def test_fingerprint_sees_a_change_with_the_same_triple_count():
    one = ScopedReader(Stub([row(A, "<urn:s>", P, '"before"')])).read([A])
    two = ScopedReader(Stub([row(A, "<urn:s>", P, '"after"')])).read([A])
    same = ScopedReader(Stub([row(A, "<urn:s>", P, '"before"')])).read([A])
    assert one.quad_count == two.quad_count
    assert one.fingerprints[A] != two.fingerprints[A]
    assert one.fingerprints[A] == same.fingerprints[A]


def test_each_read_is_a_new_snapshot():
    r = ScopedReader(Stub([row(A, "<urn:s>", P, '"x"')]))
    assert r.read([A]).id != r.read([A]).id


def test_facade_exposes_no_way_to_write():
    public = {n for n in dir(ScopedReader) if not n.startswith("_")}
    assert public == {"read", "graph_iris"}
    snap_public = {n for n in dir(ScopedReader(Stub()).read([A])) if not n.startswith("_")}
    assert not snap_public & {"update", "replace_graph", "add_to_graph", "remove_from_graph", "drop_graph", "engine"}


def test_graph_iris_lists_the_stores_graphs():
    e = Stub([{"g": f"<{B}>"}, {"g": f"<{A}>"}])
    assert ScopedReader(e).graph_iris() == [A, B]
    assert "DISTINCT ?g" in e.calls[0][0]


@pytest.mark.parametrize("bad", [[{"g": None}], [{"g": "_:b"}], ["x"]], ids=["null", "blank", "not-a-row"])
def test_graph_iris_refuses_a_malformed_answer(bad):
    with pytest.raises(EngineError):
        ScopedReader(Stub(bad)).graph_iris()
