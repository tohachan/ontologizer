"""Levels preset (data) → level individuals and generated SHACL shapes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ontologizer.core.config import GUFO, KIT

STATUSES = ("asserted", "inferred", "hypothesis", "disputed")
MAPPING_KINDS = ("equivalence", "specialization", "roleOf", "realization", "partial")
RELATION_TYPES = ("partOf", "dependsOn", "uses", "produces", "participatesIn", "mediates",
                  "owns", "triggers", "hasPhase", "associatedWith")
EVIDENCE_KINDS = ("code", "doc", "test", "config", "graph", "plan")

PREFIXES = f"""@prefix ok: <{KIT}> .
@prefix gufo: <{GUFO}> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
"""


def level_iri(level: str) -> str:
    return f"{KIT}level/{level}"


@dataclass
class Preset:
    name: str
    data: dict

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        data = yaml.safe_load(Path(path).read_text())
        return cls(name=data["name"], data=data)

    @property
    def levels(self) -> list[dict]:
        return self.data["levels"]

    @property
    def level_ids(self) -> list[str]:
        return [lv["id"] for lv in self.levels]

    @property
    def min_families(self) -> int:
        """Architectural families a shared concept needs (preset `domain_confirmation_min_projects`)."""
        return int(self.data.get("domain_confirmation_min_projects", 2))

    @property
    def categories(self) -> dict[str, dict]:
        return self.data["upper_categories"]

    def allowed_mappings(self) -> list[tuple[str, str, str]]:
        out = []
        for src, targets in self.data.get("mappings", {}).items():
            for tgt, kinds in targets.items():
                out.extend((src, tgt, k) for k in kinds)
        return sorted(out)

    def graph_levels(self, role: str) -> list[str]:
        return self.data.get("graph_levels", {}).get(role, self.level_ids)

    # ---- generated RDF ---------------------------------------------------------
    def levels_turtle(self) -> str:
        lines = [PREFIXES]
        for lv in self.levels:
            lines.append(
                f'<{level_iri(lv["id"])}> a ok:Level ; rdfs:label "{lv["label"]}"@en ; '
                f'ok:levelRank {lv["rank"]} ; ok:preset "{self.name}" .')
        return "\n".join(lines) + "\n"

    def shapes_turtle(self, role: str) -> str:
        """SHACL shapes for a graph of the given role, generated from the preset."""
        levels = " ".join(f"<{level_iri(x)}>" for x in self.graph_levels(role))
        statuses = " ".join(f"ok:{s}" for s in STATUSES)
        metas = " ".join(sorted({f"gufo:{c['meta']}" for c in self.categories.values()}))
        kinds = " ".join(f"ok:{k}" for k in MAPPING_KINDS)
        rels = " ".join(f"ok:{r}" for r in RELATION_TYPES)
        ev_kinds = " ".join(f'"{k}"' for k in EVIDENCE_KINDS)
        # SHACL forbids VALUES in sh:select, so the allowed table becomes a boolean filter.
        allowed = " || ".join(
            f"(?sl = <{level_iri(s)}> && ?tl = <{level_iri(t)}> && ?k = ok:{k})"
            for s, t, k in self.allowed_mappings())
        evidence_min = 1 if role in ("project", "domain", "task") else 0
        commit_required = role == "project"
        s = [PREFIXES, f"# Generated from preset {self.name} for role {role}. Do not edit.\n"]
        s.append(f"""
ok:ConceptShape a sh:NodeShape ; sh:targetClass ok:Concept ;
  sh:property [ sh:path skos:prefLabel ; sh:minCount 1 ; sh:message "concept needs a prefLabel" ] ;
  sh:property [ sh:path skos:definition ; sh:minCount 1 ; sh:message "concept needs a definition" ] ;
  sh:property [ sh:path ok:level ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({levels}) ;
                sh:message "concept level not allowed in a {role} graph" ] ;
  sh:property [ sh:path ok:status ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({statuses}) ] ;
  sh:property [ sh:path ok:upperCategory ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({metas}) ;
                sh:message "upper category must be a gUFO meta-type from the preset" ] ;
  sh:property [ sh:path ok:evidence ; sh:minCount {evidence_min} ; sh:class ok:Evidence ;
                sh:message "every claim about a project needs evidence" ] .

ok:EvidenceShape a sh:NodeShape ; sh:targetClass ok:Evidence ;
  sh:property [ sh:path ok:sourcePath ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path ok:sourceKind ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({ev_kinds}) ] ;
  sh:property [ sh:path ok:sourceCommit ; sh:minCount {1 if commit_required else 0} ; sh:maxCount 1 ;
                sh:message "evidence needs the source version" ] .

ok:MappingShape a sh:NodeShape ; sh:targetClass ok:Mapping ;
  sh:property [ sh:path ok:mappingSource ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:Concept ] ;
  sh:property [ sh:path ok:mappingKind ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({kinds}) ] ;
  sh:property [ sh:path ok:status ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({statuses}) ] ;
  sh:property [ sh:path ok:mappingTarget ; sh:maxCount 1 ] ;
  sh:xone (
    [ sh:path ok:mappingTarget ; sh:minCount 1 ]
    [ sh:path ok:candidateLabel ; sh:minCount 1 ]
  ) ;
  sh:sparql [
    sh:message "mapping kind not allowed between these levels by the preset" ;
    sh:select \"\"\"
      PREFIX ok: <{KIT}>
      SELECT $this WHERE {{
        $this ok:mappingSource/ok:level ?sl ; ok:mappingKind ?k ; ok:mappingTarget ?t .
        ?t ok:level ?tl .
        FILTER (!({allowed}))
      }}\"\"\" ] ;
  sh:sparql [
    sh:message "mapping target does not exist in the validation context" ;
    sh:select \"\"\"
      PREFIX ok: <{KIT}>
      SELECT $this WHERE {{
        $this ok:mappingTarget ?t . FILTER NOT EXISTS {{ ?t a ok:Concept }}
      }}\"\"\" ] .

ok:RelationShape a sh:NodeShape ; sh:targetClass ok:Relation ;
  sh:property [ sh:path ok:relFrom ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:Concept ] ;
  sh:property [ sh:path ok:relTo ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:Concept ] ;
  sh:property [ sh:path ok:relType ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({rels}) ] ;
  sh:property [ sh:path ok:status ; sh:minCount 1 ; sh:maxCount 1 ; sh:in ({statuses}) ] .

ok:DisputedShape a sh:NodeShape ;
  sh:targetSubjectsOf ok:status ;
  sh:sparql [
    sh:message "a disputed statement keeps both readings: at least two evidence nodes (rule 11.4)" ;
    sh:select \"\"\"
      PREFIX ok: <{KIT}>
      SELECT $this WHERE {{
        $this ok:status ok:disputed .
        FILTER NOT EXISTS {{ $this ok:evidence ?e1 , ?e2 . FILTER (?e1 != ?e2) }}
      }}\"\"\" ] .

ok:VariationChoiceShape a sh:NodeShape ; sh:targetClass ok:VariationChoice ;
  sh:property [ sh:path ok:choiceProject ; sh:minCount 1 ; sh:maxCount 1 ] ;
  sh:property [ sh:path ok:choiceDimension ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:VariationDimension ] ;
  sh:property [ sh:path ok:choiceValue ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:VariationValue ] ;
  sh:property [ sh:path ok:evidence ; sh:minCount 1 ] ;
  sh:sparql [
    sh:message "variation value does not belong to the chosen dimension" ;
    sh:select \"\"\"
      PREFIX ok: <{KIT}>
      PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
      SELECT $this WHERE {{
        $this ok:choiceDimension ?d ; ok:choiceValue ?v .
        FILTER NOT EXISTS {{ ?v skos:inScheme ?d }}
      }}\"\"\" ] .

ok:VariationValueShape a sh:NodeShape ; sh:targetClass ok:VariationValue ;
  sh:property [ sh:path skos:inScheme ; sh:minCount 1 ; sh:maxCount 1 ; sh:class ok:VariationDimension ] ;
  sh:property [ sh:path skos:prefLabel ; sh:minCount 1 ] ;
  sh:property [ sh:path skos:definition ; sh:minCount 1 ] .
""")
        return "\n".join(s)
