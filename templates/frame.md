---
# Machine-readable part of the frame; `ontologizer frame check` validates it.
study: my-study                # the config's `project`: one name, chosen for the topic
preset: guarino-4              # levels preset of the kit
domains:
  - id: example-domain
    label: Example domain
    boundary: What belongs to it, and what is next to it but outside.
reuse:                         # existing ontologies and standards; may be empty
  - name: gUFO
    url: http://purl.org/nemo/gufo
    use: upper categories
families:                      # the comparison corpus by architectural family
  family-a:
    label: First architectural family
    projects: [project-1, project-2]
  family-b:
    label: Second architectural family
    projects: [project-3]
# admission_families: 1         # optional: families a concept needs to enter the shared levels (default: the
                               # preset's confirmation threshold); below that threshold it stays a hypothesis
# sources:                       # optional, per project: `description` for a text about a subject, not a system
#   project-3: {kind: description, origin: where the text comes from}   # a retelling adds `derived_from: <source>`
# constraints:                   # optional: also extract constraints (docs/process.md §Constraints)
#   scope: The invariants and promise boundaries each project states as such.
overlay: []                    # projects mapped onto a release later; never read while building shared levels
competency_questions:
  - id: cq1
    level: domain              # domain | task | application | variation
    question: Which projects realize concept X, and how?
    # query: queries/cq1.rq    # optional: SPARQL that answers it mechanically (rows > 0 = answered)
dimensions:                    # optional: the second axis; every extraction reports a value with evidence
  - id: example-dimension
    label: Example dimension
    definition: A design decision projects differ on.
    values:
      - {id: option-a, label: Option A, definition: One way to decide it.}
      - {id: option-b, label: Option B, definition: Another way.}
targets: []                    # optional design targets: [{id, label, description}]; tag requirements as
                               # competency questions with `target: <id>`; the report gives a fit matrix
out_of_scope:
  - What the study deliberately does not cover.
---

# Frame: <study>

## Domains

For each domain: what it is, its boundary, and the neighbouring domains it is kept apart from.

## Reuse

Existing ontologies and standards, what is taken from each and why; what was considered and rejected.

## Corpus

The projects, grouped into architectural families, with the reason each project belongs to its family. Projects of one family count once as support for a shared concept.

## Competency questions

What the finished ontology must answer. Each question has an id, a level and, when it can be answered mechanically, a query.

## Levels

The levels preset and any deviation from it.

## Overlay

The project(s) that will be mapped onto a release of the shared levels and must not be read while building them.

## Out of scope

What the study does not cover.
