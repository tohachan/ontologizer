---
name: query-ontology
description: Use whenever a question is put to an ontology, knowledge graph or concept model held in the ontologizer / Open Ontologies store — asking what it says, exploring or reasoning over its concepts, relations, levels, mappings, variation or evidence, checking one's own concept, design or text against it, comparing projects or releases in it, or answering strictly in its vocabulary. Triggers include "what does the ontology say about X", "is concept Y in the ontology", "which projects have Z", "check this against the ontology", "use only the ontology's terms", and Russian phrasings such as «спроси онтологию», «что в онтологии про X», «есть ли в онтологии понятие», «проверь против онтологии», «сверься с онтологией», «отвечай только в терминах онтологии», «какие проекты реализуют». Read-only SPARQL. Not for building or changing the ontology (analyse-corpus, map-repo, ingest, release).
---

# query-ontology

Talking to an ontology means turning each question into small SPARQL queries, reading the answers and reasoning over them, and saying which part of the answer came from the store and which part is your own reasoning. This skill only reads. Never write to the store from here, not even through REST. Changes go through the owner's extraction files and `ingest`/`release`.

Query patterns, tested against a live store, are in [`references/queries.md`](references/queries.md). Copy one and change the placeholders. Do not write a new query from memory when a pattern already exists.

## Running a query

- **Locate the kit.** It is the repository that holds this skill. When the skill is installed by symlink, it is two levels above the resolved skill file: `KIT=$(cd "$(dirname "$(readlink -f <this SKILL.md>)")/../.." && pwd)`.
- **From an owner's directory** (a study with its own `ontologizer.yaml`): `uv run --project "$KIT" ontologizer query 'SPARQL'` or `… query FILE.rq`.
- **From anywhere else**, point at the hub config: `uv run --project "$KIT" ontologizer --config "$KIT/ontologizer.yaml" query 'SPARQL'`. Without a config the CLI stops with "no ontologizer.yaml at or above".
- Output is one row per line, `var=value | var=value`, with values in N-Triples form (`<iri>`, `"text"@en`).
- **Raw REST** (scripts, no Python): `"$KIT/scripts/oo" curl api/query -X POST -H 'Content-Type: application/json' --data '{"query":"…"}'`. The SPARQL goes in the `query` field. Never print the token.
- Only SELECT, ASK and CONSTRUCT. Never call `onto_load`, `onto_clear`, `onto_apply`, `onto_plan`, `onto_rollback`, `onto_reason` with `materialize`, or `onto_align` without `dry_run`: they act on the whole store (`docs/open-ontologies.md`).
- Keep results small: use `LIMIT`, aggregates and `GROUP_CONCAT`. Do not dump a graph into the context when a count or a list of labels answers the question.

## What the store holds

Everything is in named graphs `urn:ontologizer:graph:<path>`. Kit terms use the prefix `ok: <urn:ontologizer:kit:>`.

| Graph path | Holds |
| --- | --- |
| `<owner>/domain`, `<owner>/task`, `<owner>/application` | Shared concepts of one Guarino level, consolidated from several projects |
| `<owner>/variation` | Variation dimensions (`skos:ConceptScheme`) and their values |
| `projects/<name>` | One project's own concepts, its mappings to shared concepts, its relations and variation choices |
| `upper/gufo`, `kit/core` | Upper ontology and kit vocabulary |
| `<path>@<ver>` | Frozen release of `<path>`. Its terms have their own IRIs: `urn:ontologizer:<path>@<ver>:<slug>` |

The shapes you will query:

- **Concept**: `a ok:Concept, owl:Class, <gUFO meta-type>`, with `skos:prefLabel`, `skos:altLabel` (synonyms, project names), `skos:definition`, `ok:level` (`ok:level/domain|task|application`), `ok:upperCategory` (gufo:Kind, Role, EventType, …), `rdfs:subClassOf`, `ok:status`, `ok:note`, `ok:evidence`. Project concepts also have `ok:inProject`.
- **Mapping** (project concept → shared concept): `ok:mappingSource`, `ok:mappingTarget`, `ok:mappingKind` (equivalence, specialization, roleOf, realization, partial), `ok:status`, `ok:confidence`, `ok:note`. `ok:candidateLabel` means the target is proposed and not yet in a shared graph.
- **Relation**: `ok:relFrom`, `ok:relTo`, `ok:relType` (partOf, dependsOn, uses, produces, participatesIn, mediates, owns, triggers, hasPhase, associatedWith), `rdfs:label` (the verb phrase as read), `ok:status`, `ok:evidence`.
- **VariationChoice**: `ok:choiceProject`, `ok:choiceDimension`, `ok:choiceValue`, `ok:note`, `ok:evidence`.
- **Evidence**: `ok:sourceKind` (code, doc, test, config, graph, plan), `ok:sourcePath`, sometimes `ok:sourceRepo`, `ok:sourceLines`, `ok:sourceCommit`.
- **Status** (`ok:status`): `ok:asserted`, `ok:inferred`, `ok:hypothesis`, `ok:disputed`. Always say the status when it is not `asserted`.

## Slice: which graphs an answer reads

A query over all graphs mixes the working state with the history: each project and each shared level is stored once as a working graph and once per release. Choose the slice yourself, so the user can ask in plain language:

- **Default: the working graphs** of the study, the ones without `@`: the projects in question plus the shared `domain`/`task`/`application`/`variation` graphs. Add `FILTER(!CONTAINS(STR(?g), "@"))` to every `GRAPH ?g`.
- **A release only when it is asked for explicitly** ("in 0.2", "as released", "compare with 0.1"). Then read the **whole set of graphs released under that version** (Q13), never a single `@VER` graph mixed with working graphs. That is what makes a release analysis reproducible.
- **"Current" means last ingested.** The working graphs hold whatever was last written with `ingest`. Edited extraction JSON and new upstream commits do not reach the store on their own. When the user asks about the latest code, say which commit was analysed and do not claim more.

Every answer starts with one line naming the slice and the analysed commits of the projects it used (Q13), e.g. `Slice: working graphs; projects/a @ 466aa41, projects/b @ 722f3eb`.

Rules of thumb:
- **Look labels up by IRI, not by graph.** A project graph can point at release terms (an overlay maps onto `…@<ver>:…`). The IRI already says which version it is.
- **Match by meaning, not only by name.** Search `prefLabel`, `altLabel` and `definition` together. Projects name the same thing differently, and the synonyms are in `altLabel`.

## Orientation (once per conversation)

1. `ontologizer status`, or the graph list query (Q1), shows which graphs exist and how big they are.
2. Fix the scope: which owner and which levels. Use the working graphs unless the user names a release (see Slice). Get the analysed commits (Q13).
3. For a small shared level (up to about 150 concepts), load its glossary (Q2) once and keep it in context. Answer from it, and query only for relations, mappings and evidence.

## Mode 1: exploring and reasoning

The user asks open questions such as "how is X organised", "what is X related to", "where does the shared model disagree with project P".

- Resolve every term in the question to IRIs first (Q3). If it is ambiguous, show the candidates with their definitions and ask the user which one they mean.
- Build the answer from concept cards (Q4), neighbours (Q5), the hierarchy (Q6), what realises the concept in each project (Q7) and variation (Q9).
- Reason out loud, but keep two layers apart: **"The ontology says"** (facts with IRIs and status) and **"My inference"** (your conclusions from those facts). An inference that would need a new concept, relation or mapping is a proposal for the owner, not a fact.
- Count support: a shared concept that 2 projects realise is not the same as one that 8 realise. Q7 gives these counts.

## Mode 2: checking the user's concept against the ontology

The user describes a concept ("we have a Workspace that…") and asks whether it exists, how it relates to what exists, or whether the model breaks.

1. Write the user's concept down as a one-sentence definition and its likely gUFO meta-type (kind, role, event, relator, …). Confirm this with the user if it is unclear.
2. Find candidates by labels, synonyms and keywords from the definition (Q3). Try at least two or three phrasings. Then read each candidate's card (Q4).
3. Give one verdict per candidate, using the kit's mapping kinds:
   - **equivalence**: the same concept under another name;
   - **specialization**: every instance of the user's concept is an instance of the candidate;
   - **role**: the user's concept is a role that instances of the candidate play;
   - **realization**: the user's concept is a mechanism or component that implements the candidate;
   - **partial**: the two overlap and neither includes the other;
   - **absent**: nothing fits. Say what is closest and why it does not fit.
4. Check consistency: compare the meta-type with `ok:upperCategory` (a Kind cannot be equivalent to an EventType), and compare the user's claimed relations with the candidate's neighbours (Q5). Point out every conflict.
5. Show how projects realise the candidate (Q7). It tells the user whether their reading is common or unusual.

Output a short table with these columns: user's concept, candidate (label + IRI), verdict, reason, conflicts, support.

## Mode 3: staying inside the vocabulary

The user wants an answer, a review or a text that uses only the ontology's concepts: "check this design doc against the vocabulary", "describe our system using only the shared terms".

1. Fix the vocabulary: which graphs (usually the shared levels plus variation) and which version. Load the glossary of that scope (Q2, Q10 for variation). Mention the version in the answer.
2. Use only terms from that glossary. Write each term's `prefLabel` at least once, and its IRI when the reader needs to look it up. Use the relation types and labels from the store, not new verbs.
3. When something cannot be said with the vocabulary, do not invent a term and do not force it onto the nearest concept. Mark it as **outside the vocabulary**, give the closest concept and the reason it does not fit. Collect these gaps at the end, because they are input for the next extraction.
4. When reviewing a text, go through its nouns and processes. Map each one to a concept (as in mode 2), or list it as a gap or as a synonym that should become an `altLabel`.

When another agent has to work inside the vocabulary (for example a subagent writing a design), give it the glossary (Q2 output) and the rules of step 3 in its prompt. Do not give it access to the store.

## Answer format

- First line: the slice and the analysed commits (see Slice).
- Cite concepts as `Label` (`<iri>`). Give the status when it is not `asserted`, the level when it matters, and the version when you used a release.
- When the user asks "why" or "where from", show the evidence (Q8): source kind and path.
- When the store has no answer, say that it has no answer. Do not fill the gap from general knowledge without labelling it as your own.
- Save a query that is worth repeating (a competency question, a recurring check) under the owner's `queries/` as an `.rq` file, and tell the user its path. The kit itself holds no study queries.
