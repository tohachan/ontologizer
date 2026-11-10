"""Projection benchmark on synthetic views of 50, 100 and 200 nodes (docs/visualizer.md, Verification).

    uv run scripts/views_bench.py

Measures normalization (read_model on a parsed snapshot) and projection (compile_view) separately;
the source read and the browser's layout and render are measured elsewhere (scripts/views_smoke.py,
web/visualizer bench). Synthetic data only; no store.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "views"))
from helpers import snapshot  # noqa: E402

from ontologizer.core.views.project import compile_view  # noqa: E402
from ontologizer.core.views.reader import read_model  # noqa: E402
from ontologizer.core.views.scope import GraphEntry, Scope  # noqa: E402
from ontologizer.core.views.spec import parse  # noqa: E402

G = "urn:ontologizer:graph:bench"
NS = "urn:sample:bench:"


def trig(n: int) -> str:
    out = ["@prefix ok: <urn:ontologizer:kit:> .", "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
           "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", f"<{G}> {{"]
    for i in range(n):
        out.append(f'<{NS}C{i}> a ok:Concept ; skos:prefLabel "Concept {i}"@en, "Понятие {i}"@ru ; '
                   f'ok:level <urn:ontologizer:kit:level/domain> ; ok:status ok:asserted .')
        for j, k in ((1, i + 1), (2, (i * 7 + 3) % n)):
            if k < n and k != i:
                out.append(f"<{NS}r{i}_{j}> a ok:Relation ; ok:relFrom <{NS}C{i}> ; ok:relTo <{NS}C{k}> ; "
                           f"ok:relType ok:uses ; ok:status ok:asserted ; ok:evidence [ ok:sourcePath \"f{i}.py\" ] .")
        if i % 5:
            out.append(f"<{NS}C{i}> rdfs:subClassOf <{NS}C{i - i % 5}> .")
    return "\n".join(out + ["}"])


def spec(n: int):
    return parse({"schemaVersion": 1, "id": "bench", "title": "Bench", "scope": {"primaryGraphs": ["bench"], "contextGraphs": []},
                  "selection": {"entities": [], "excludedEntities": [], "levels": ["domain"],
                                "relationKinds": ["kit-relation", "class-inheritance"], "relationTypeIris": [],
                                "expansion": {"hops": 0, "direction": "both"}},
                  "groups": [{"id": "g1", "title": "First", "members": [f"{NS}C{i}" for i in range(min(10, n))], "collapsed": True}],
                  "summaries": [], "annotations": [],
                  "presentation": {"languages": ["ru", "en"], "direction": "RIGHT", "maxNodes": 200, "maxEdges": 400,
                                   "preset": "domain"}})


def main() -> int:
    scope = Scope("o", (GraphEntry("bench", G, "domain", "domain", "o", "bench", None),), ())
    print("| nodes | edges | quads | normalize (median of 5) | project (median of 20) |")
    print("| --- | --- | --- | --- | --- |")
    for n in (50, 100, 200):
        snap = snapshot(trig(n))
        norm = []
        for _ in range(5):
            t = time.perf_counter()
            model = read_model(snap, ("ru", "en"))
            norm.append(time.perf_counter() - t)
        s = spec(n)
        proj = []
        for _ in range(20):
            t = time.perf_counter()
            d = compile_view(s, scope, model)
            proj.append(time.perf_counter() - t)
        print(f"| {n} | {len(d.edges)} | {snap.quad_count} | {statistics.median(norm) * 1000:.1f} ms | "
              f"{statistics.median(proj) * 1000:.1f} ms |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
