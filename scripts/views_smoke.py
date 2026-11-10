"""Read-only smoke check of the visualizer against the configured engine (opt-in; not part of pytest).

    uv run scripts/views_smoke.py [--owner NAME] [--hub DIR]

Runs the service's handler in process against the store this hub names: catalog, search, compile
on the search's snapshot, element details. It sends only SELECT queries and writes nothing, not
even a saved view. Prints timings; exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ontologizer.adapters.engine.open_ontologies import OpenOntologies
from ontologizer.adapters.visualization.app import Settings, Visualizer
from ontologizer.core.config import read_hub
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import ScopedReader

HOST = "127.0.0.1:8090"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--owner")
    ap.add_argument("--hub", type=Path, default=Path(__file__).resolve().parents[1])
    a = ap.parse_args(argv)
    hub = a.hub.resolve()
    app = Visualizer(Owners(hub), ScopedReader(OpenOntologies((read_hub(hub).get("store") or {}).get("url"))),
                     Settings(allowed_hosts=(HOST,)))

    def call(method, path, body=None):
        t = time.perf_counter()
        r = app.handle(method, "/visualizer/api/" + path, {"Host": HOST, "Content-Type": "application/json"},
                       json.dumps(body).encode() if body is not None else b"")
        out = json.loads(r.body)
        if r.status != 200:
            raise SystemExit(f"FAIL {method} {path.split('?')[0]}: {r.status} {out['error']['code']}")
        print(f"ok   {method:4} {path.split('?')[0]:28} {(time.perf_counter() - t) * 1000:7.0f} ms")
        return out

    health = call("GET", "health")
    if health["engine"] != "up":
        raise SystemExit(f"FAIL engine: {health['engine']}")
    owners = [o["name"] for o in call("GET", "catalog")["owners"] if o["available"]]
    owner = a.owner or (owners[0] if owners else None)
    if owner not in owners:
        raise SystemExit("FAIL no available owner")
    cat = call("GET", f"catalog?owner={owner}")
    own = sorted(g["path"] for g in cat["graphs"] if g["own"] and not g["release"])
    if not own:
        raise SystemExit(f"FAIL {owner}: no working graph of its own")
    ctx = [g["path"] for g in cat["graphs"] if g["path"] in ("upper/gufo", "kit/core")]
    scope = {"primaryGraphs": own[:1], "contextGraphs": ctx}
    found = call("POST", "search", {"owner": owner, "scope": scope, "query": "e", "limit": 5})
    if not found["results"]:
        raise SystemExit("FAIL search found nothing")
    spec = {"schemaVersion": 1, "id": "smoke", "title": "Smoke", "scope": scope,
            "selection": {"entities": [found["results"][0]["iri"]], "excludedEntities": [], "levels": [],
                          "relationKinds": ["kit-relation", "kit-mapping", "class-inheritance"], "relationTypeIris": [],
                          "expansion": {"hops": 2, "direction": "both"}},
            "groups": [], "summaries": [], "annotations": [],
            "presentation": {"languages": ["en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160, "preset": "domain"}}
    c = call("POST", "compile", {"owner": owner, "spec": spec, "snapshotId": found["snapshotId"]})
    node = c["diagram"]["nodes"][0]["id"]
    call("GET", f"compilations/{c['compileId']}/elements/{node}")
    n = c["diagram"]["counts"]
    print(f"owner {owner}, graph {own[0]}: {n['shownNodes']} nodes, {n['shownEdges']} edges, "
          f"{len(c['manifest']['fingerprints'])} graphs read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
