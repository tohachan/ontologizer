"""The normalized source model a view is projected from. Plain data, ordered, no RDF objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    value: str
    language: str | None
    prop: str                      # "pref" (skos:prefLabel), "rdfs" (rdfs:label) or "alt" (skos:altLabel)


@dataclass(frozen=True)
class Evidence:
    repo: str | None
    path: str | None
    lines: str | None
    commit: str | None
    kind: str | None


@dataclass(frozen=True)
class Entity:
    iri: str
    types: tuple[str, ...]
    graphs: tuple[str, ...]        # graphs in which it is a subject
    labels: tuple[Label, ...]
    definitions: tuple[Label, ...]
    level: str | None              # preset level id
    upper_category: str | None     # IRI of its gUFO meta-type
    status: str | None
    project: str | None
    evidence: tuple[Evidence, ...]
    owl_structure: bool            # has OWL structure the diagram does not show (restrictions, class expressions)


@dataclass(frozen=True)
class Relationship:
    id: str
    kind: str                      # kit-relation | kit-mapping | class-inheritance | rdf-assertion
    source: str
    target: str | None             # None only for a mapping candidate
    type: str                      # relType, mappingKind, or the RDF predicate
    graph: str
    record: str | None             # the reified record, for kit relations and mappings
    status: str | None
    label: str | None
    evidence: tuple[Evidence, ...]
    rule: str
    candidate_label: str | None = None
    candidate_level: str | None = None


@dataclass(frozen=True)
class VariationChoice:
    record: str
    graph: str
    dimension: str
    value: str
    status: str | None
    note: str | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Project:
    iri: str
    labels: tuple[Label, ...]
    repository: str | None
    commit: str | None
    variation: tuple[VariationChoice, ...]


@dataclass(frozen=True)
class Diagnostic:
    code: str                      # incomplete-record | conflicting-record
    message: str
    subject: str
    graph: str


@dataclass(frozen=True)
class SourceModel:
    snapshot_id: str
    entities: Mapping[str, Entity]                 # by IRI, in IRI order
    relationships: tuple[Relationship, ...]
    candidates: tuple[Relationship, ...]           # mappings with only a candidate label
    projects: Mapping[str, Project]
    diagnostics: tuple[Diagnostic, ...]
