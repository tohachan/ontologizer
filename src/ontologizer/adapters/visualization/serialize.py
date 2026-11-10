"""JSON shapes of the API: dataclasses to camelCase dictionaries, RDF terms as tagged values."""

from __future__ import annotations

import dataclasses
from typing import Any

from rdflib import BNode, Literal, URIRef


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def to_json(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {camel(f.name): to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {k if isinstance(k, str) else str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    return value


def term(t, snapshot_id: str) -> dict:
    if isinstance(t, URIRef):
        return {"kind": "iri", "value": str(t)}
    if isinstance(t, BNode):
        return {"kind": "blank", "value": str(t), "snapshotId": snapshot_id}
    if isinstance(t, Literal):
        out = {"kind": "literal", "value": str(t),
               "datatype": str(t.datatype) if t.datatype else
               ("http://www.w3.org/1999/02/22-rdf-syntax-ns#langString" if t.language else
                "http://www.w3.org/2001/XMLSchema#string")}
        if t.language:
            out["language"] = t.language
        return out
    raise TypeError(f"not an RDF term: {t!r}")
