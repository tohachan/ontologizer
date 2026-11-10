"""Turn synthetic TriG into the engine's row format, so reader tests run the real read path."""

from __future__ import annotations

import re

from rdflib import Dataset

from ontologizer.core.views.source import Limits, ScopedReader

_TAIL = re.compile(r"^(?P<spo>.*) (?P<g><[^>]*>) \.$")


class RowEngine:
    def __init__(self, trig: str):
        ds = Dataset()
        ds.parse(data=trig, format="trig")
        self.rows = []
        for line in ds.serialize(format="nquads").splitlines():
            m = _TAIL.match(line.strip())
            if not m:
                continue                      # default-graph statements: not part of any scope
            s, p, o = m["spo"].split(" ", 2)
            self.rows.append({"g": m["g"], "s": s, "p": p, "o": o})
        self.graphs = sorted({r["g"][1:-1] for r in self.rows})

    error = None                                   # an exception to raise instead of answering
    reads = 0

    def select_bounded(self, sparql, max_bytes, timeout):
        if self.error:
            raise self.error
        self.reads += 1
        if "DISTINCT ?g" in sparql:
            return [{"g": f"<{g}>"} for g in self.graphs]
        wanted = set(re.findall(r"<([^>]+)>", sparql.split("VALUES", 1)[1].split("}", 1)[0]))
        return [r for r in self.rows if r["g"][1:-1] in wanted]


def snapshot(trig: str, graphs=None):
    e = RowEngine(trig)
    return ScopedReader(e, Limits()).read(graphs or e.graphs)
