# Query patterns

Every pattern starts with these prefixes (omitted below):

```sparql
PREFIX ok:   <urn:ontologizer:kit:>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX gufo: <http://purl.org/nemo/gufo#>
PREFIX dcterms: <http://purl.org/dc/terms/>
```

Placeholders: `GRAPH_PATH` is a graph path such as `<owner>/domain` or `projects/<name>`. `CONCEPT` is a full concept IRI. `PATTERN` is a case-insensitive regex.

Engine gotchas:
- `GROUP_CONCAT` over IRIs returns nothing. Concatenate `STR(?x)` or a label instead.
- Counts come back as `"5"^^xsd:integer`.
- `GRAPH ?g { … }` without a filter also matches releases, so add `FILTER(!CONTAINS(STR(?g), "@"))` for working graphs only.

## Q1. Graphs and their size

```sparql
SELECT ?g (COUNT(*) AS ?triples) WHERE { GRAPH ?g { ?s ?p ?o } }
GROUP BY ?g ORDER BY ?g
```

Concepts per graph and level:

```sparql
SELECT ?g ?level (COUNT(?c) AS ?concepts) WHERE {
  GRAPH ?g { ?c a ok:Concept ; ok:level ?level }
  FILTER(!CONTAINS(STR(?g), "@"))
} GROUP BY ?g ?level ORDER BY ?g
```

## Q2. Glossary of one graph

```sparql
SELECT ?c ?label ?type ?def
       (GROUP_CONCAT(DISTINCT STR(?alt); separator="; ") AS ?synonyms) WHERE {
  GRAPH <urn:ontologizer:graph:GRAPH_PATH> {
    ?c a ok:Concept ; skos:prefLabel ?label ; skos:definition ?def ; ok:upperCategory ?type .
    OPTIONAL { ?c skos:altLabel ?alt }
  }
} GROUP BY ?c ?label ?type ?def ORDER BY ?label
```

For a large scope, drop `?def` and `?synonyms` first, then read cards (Q4) only for the concepts you need.

## Q3. Find a concept by name, synonym or definition

```sparql
SELECT ?g ?c ?label (SAMPLE(STR(?t)) AS ?matched) WHERE {
  GRAPH ?g {
    ?c a ok:Concept ; skos:prefLabel ?label .
    { ?c skos:prefLabel ?t } UNION { ?c skos:altLabel ?t } UNION { ?c skos:definition ?t }
    FILTER(REGEX(STR(?t), "PATTERN", "i"))
  }
  FILTER(!CONTAINS(STR(?g), "@"))
} GROUP BY ?g ?c ?label ORDER BY ?g LIMIT 40
```

Use alternation for phrasings, e.g. `"merge.?queue|integration queue"`. To search only shared levels, add `FILTER(!STRSTARTS(STR(?g), "urn:ontologizer:graph:projects/"))`.

## Q4. Concept card

```sparql
SELECT ?p ?o WHERE { GRAPH ?g { CONCEPT ?p ?o } FILTER(?p != ok:evidence) }
```

The card gives the labels, definition, level, meta-type, superclasses, status and note. The evidence is in Q8.

## Q5. Neighbours (relations in and out)

```sparql
SELECT ?dir ?type ?verb ?other ?otherLabel ?status WHERE {
  GRAPH ?g {
    ?r a ok:Relation ; ok:relType ?type ; ok:relFrom ?f ; ok:relTo ?t .
    OPTIONAL { ?r rdfs:label ?verb } OPTIONAL { ?r ok:status ?status }
    FILTER(?f = CONCEPT || ?t = CONCEPT)
  }
  FILTER(!CONTAINS(STR(?g), "@"))
  BIND(IF(?f = CONCEPT, "out", "in") AS ?dir)
  BIND(IF(?f = CONCEPT, ?t, ?f) AS ?other)
  OPTIONAL { GRAPH ?og { ?other skos:prefLabel ?otherLabel } }
} ORDER BY ?dir ?type
```

Read an `out` row as "CONCEPT *verb* other" and an `in` row as "other *verb* CONCEPT".

## Q6. Hierarchy

Superclasses up to the gUFO root:

```sparql
SELECT ?sup ?supLabel WHERE {
  GRAPH ?g { CONCEPT rdfs:subClassOf+ ?sup }
  OPTIONAL { GRAPH ?sg { ?sup skos:prefLabel ?supLabel } }
}
```

Direct subclasses:

```sparql
SELECT ?sub ?label ?g WHERE {
  GRAPH ?g { ?sub rdfs:subClassOf CONCEPT ; skos:prefLabel ?label }
  FILTER(!CONTAINS(STR(?g), "@"))
}
```

## Q7. How projects realise a shared concept (support)

```sparql
SELECT ?pg ?src ?srcLabel ?kind ?status ?confidence WHERE {
  GRAPH ?pg {
    ?m ok:mappingTarget CONCEPT ; ok:mappingSource ?src ; ok:mappingKind ?kind .
    OPTIONAL { ?m ok:status ?status } OPTIONAL { ?m ok:confidence ?confidence }
    OPTIONAL { ?src skos:prefLabel ?srcLabel }
  }
  FILTER(!CONTAINS(STR(?pg), "@"))
} ORDER BY ?pg
```

Support of every shared concept in a graph (how many projects map to it):

```sparql
SELECT ?c ?label (COUNT(DISTINCT ?pg) AS ?projects) WHERE {
  GRAPH <urn:ontologizer:graph:GRAPH_PATH> { ?c a ok:Concept ; skos:prefLabel ?label }
  OPTIONAL { GRAPH ?pg { ?m ok:mappingTarget ?c } FILTER(!CONTAINS(STR(?pg), "@")) }
} GROUP BY ?c ?label ORDER BY DESC(?projects)
```

The reverse direction (a project concept → where it maps):

```sparql
SELECT ?target ?targetLabel ?kind ?candidate WHERE {
  GRAPH ?g { ?m ok:mappingSource CONCEPT ; ok:mappingKind ?kind .
             OPTIONAL { ?m ok:mappingTarget ?target } OPTIONAL { ?m ok:candidateLabel ?candidate } }
  OPTIONAL { GRAPH ?tg { ?target skos:prefLabel ?targetLabel } }
}
```

## Q8. Evidence of a concept, relation or choice

```sparql
SELECT ?kind ?path ?lines ?repo WHERE {
  GRAPH ?g { CONCEPT ok:evidence ?e . ?e ok:sourceKind ?kind ; ok:sourcePath ?path .
             OPTIONAL { ?e ok:sourceLines ?lines } OPTIONAL { ?e ok:sourceRepo ?repo } }
}
```

In a shared graph, `sourceKind "graph"` with a path `projects/<name>#<slug>` points at the project concept it was consolidated from.

## Q9. Variation: which project chose what

```sparql
SELECT ?dimL ?valL (COUNT(DISTINCT ?proj) AS ?n)
       (GROUP_CONCAT(DISTINCT STRAFTER(STR(?proj), "project:"); separator=", ") AS ?projects) WHERE {
  GRAPH ?g { ?ch a ok:VariationChoice ; ok:choiceDimension ?dim ; ok:choiceValue ?val ; ok:choiceProject ?proj }
  FILTER(!CONTAINS(STR(?g), "@"))
  GRAPH ?vg { ?dim skos:prefLabel ?dimL . ?val skos:prefLabel ?valL }
} GROUP BY ?dimL ?valL ORDER BY ?dimL DESC(?n)
```

For one project, add `FILTER(?g = <urn:ontologizer:graph:projects/NAME>)` and select `?note`.

## Q10. Variation glossary (dimensions and values)

```sparql
SELECT ?dim ?dimL ?val ?valL ?def WHERE {
  GRAPH <urn:ontologizer:graph:GRAPH_PATH> {
    ?val skos:inScheme ?dim ; skos:prefLabel ?valL .
    ?dim skos:prefLabel ?dimL .
    OPTIONAL { ?val skos:definition ?def }
  }
} ORDER BY ?dimL ?valL
```

## Q11. Open questions in the model

Hypotheses and disputed statements:

```sparql
SELECT ?g ?status (COUNT(*) AS ?n) WHERE {
  GRAPH ?g { ?x ok:status ?status FILTER(?status != ok:asserted) }
  FILTER(!CONTAINS(STR(?g), "@"))
} GROUP BY ?g ?status ORDER BY ?g
```

Mappings to targets that do not exist yet:

```sparql
SELECT ?g ?src ?candidate ?level WHERE {
  GRAPH ?g { ?m ok:mappingSource ?src ; ok:candidateLabel ?candidate . OPTIONAL { ?m ok:candidateLevel ?level } }
  FILTER(!CONTAINS(STR(?g), "@"))
} ORDER BY ?candidate
```

## Q12. Release against the working graph

A release term links to its working term with `dcterms:isVersionOf`:

```sparql
SELECT ?w ?inRelease ?inWorking WHERE {
  {
    GRAPH <urn:ontologizer:graph:GRAPH_PATH@VER> { ?c a ok:Concept ; skos:prefLabel ?inRelease ; dcterms:isVersionOf ?w }
    OPTIONAL { GRAPH <urn:ontologizer:graph:GRAPH_PATH> { ?w skos:prefLabel ?inWorking } }
  } UNION {
    GRAPH <urn:ontologizer:graph:GRAPH_PATH> { ?w a ok:Concept ; skos:prefLabel ?inWorking }
    FILTER NOT EXISTS { GRAPH <urn:ontologizer:graph:GRAPH_PATH@VER> { ?c dcterms:isVersionOf ?w } }
  }
} ORDER BY ?w
```

A row with no `?inWorking` means the concept was removed after the release. A row with no `?inRelease` means it was added. Different labels mean it was renamed; compare the definitions with Q4 on both IRIs. To compare by meaning rather than by identity, use `ontologizer report --compare` (docs/process.md).

## Q13. Slice: analysed commits and release sets

Analysed commit of each project in the working graphs (state as of the last `ingest`):

```sparql
SELECT ?g ?commit ?repo WHERE {
  GRAPH ?g { ?p a ok:Project . OPTIONAL { ?p ok:analysedCommit ?commit } OPTIONAL { ?p ok:repository ?repo } }
  FILTER(!CONTAINS(STR(?g), "@"))
} ORDER BY ?g
```

For a release, put the release graph in place of the filter (`FILTER(STRENDS(STR(?g), "@VER"))`).

Which graphs make up each release version. A release analysis reads exactly this set:

```sparql
SELECT ?ver (GROUP_CONCAT(?of; separator=", ") AS ?graphs) WHERE {
  GRAPH ?g { ?r a ok:Release ; ok:releaseVersion ?ver ; ok:releaseOf ?of }
} GROUP BY ?ver ORDER BY ?ver
```

To restrict a query to one release set, use `FILTER(STRENDS(STR(?g), "@VER"))` in place of the working-graph filter.

## Q14. Constraints on a concept

What a project guarantees, forbids, permits or does not promise about a concept: the constraints that constrain it, and those whose statement only names it. Absent where the study did not extract constraints.

```sparql
SELECT ?g ?k ?statement ?modality ?role ?src ?status WHERE {
  GRAPH ?g {
    ?k a ok:Constraint ; ok:statement ?statement ; ok:modality ?modality .
    { ?k ok:constrains <CONCEPT_IRI> BIND("constrains" AS ?role) }
    UNION { ?k ok:usesTerm <CONCEPT_IRI> FILTER NOT EXISTS { ?k ok:constrains <CONCEPT_IRI> } BIND("names" AS ?role) }
    OPTIONAL { ?k ok:sourceIdentifier ?src } OPTIONAL { ?k ok:status ?status }
  }
  FILTER(!CONTAINS(STR(?g), "@"))
} ORDER BY ?g ?modality
```

Which projects state a constraint of each modality on concepts mapped to one shared concept (the same question across projects; matching the constraints themselves is not part of the model):

```sparql
SELECT ?g ?modality (COUNT(DISTINCT ?k) AS ?n) WHERE {
  GRAPH ?g { ?m ok:mappingSource ?pc ; ok:mappingTarget <SHARED_CONCEPT_IRI> .
             ?k a ok:Constraint ; ok:constrains ?pc ; ok:modality ?modality }
  FILTER(!CONTAINS(STR(?g), "@"))
} GROUP BY ?g ?modality ORDER BY ?g
```

Terms the statements need that have no concept yet (input for the next extraction):

```sparql
SELECT ?g ?term (COUNT(?k) AS ?n) WHERE {
  GRAPH ?g { ?k a ok:Constraint ; ok:candidateTerm ?term }
  FILTER(!CONTAINS(STR(?g), "@"))
} GROUP BY ?g ?term ORDER BY DESC(?n)
```

Concepts of a project graph that no constraint is about (where its study extracted constraints at all):

```sparql
SELECT ?c ?label WHERE {
  GRAPH <urn:ontologizer:graph:GRAPH_PATH> {
    ?c a ok:Concept ; skos:prefLabel ?label .
    FILTER EXISTS { ?any a ok:Constraint }
    FILTER NOT EXISTS { ?k ok:constrains ?c }
  }
} ORDER BY ?label
```
