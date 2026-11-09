# Extraction brief: {project} ({extractor_id}{part_label})

You extract the concepts of one source into extraction JSON. You do not write RDF and you do not write to any store. This is extraction {extractor_id} of run `{run}`; at least one other model extracts the same source independently. Do not look for, read or wait for the other extraction.

## The study

{frame_summary}

The source is a **description**: a text about a subject, in any form (prose, notes, a table, a formal model). It is not the implementation of a system, so everything in it is **what its author claims**, not an observed fact. Work in the source's own terms: name each concept as the source names it and define it as the source uses it. There is no shared vocabulary in this stage; mapping to shared concepts is a later stage.

Out of scope for the study: {out_of_scope}

{never_read}

## The source

- Repository `{project}` at commit `{commit}`, checked out at `{source_dir}`. Cite only this commit. Do not modify the checkout, and do not run anything in it.
- {inventory_line}

{inventory_block}

## What to extract

The concepts the source defines or relies on: the things it names, the processes it describes, the distinctions it draws. {cap_line}

**Judge the source's structure, do not copy it.** A formal model states its author's choices:
- its class hierarchy is a claim: keep a `broader` link when the text supports it, and say so in `note` when you doubt it;
- its upper-level typing does not decide `upper_category`: decide that from what the thing is;
- a term that exists only to name an example (one sample system, one sample configuration) is not a concept. Report such terms in `caveats`;
- a term that only states a value or a preference of the author is not a concept either, unless the source defines a thing behind it.

**Report what JSON cannot carry.** Put into `caveats`, one entry per item, with its lines: rules and constraints (what must or must not hold), disjointness, numeric limits, and recommendations or warnings about what is right or wrong. A normative statement does not become a fact about the subject: when a concept carries one, keep it out of the definition and name it in `note`.

**Granularity.** One concept per thing, not per variant: variants that the source does not treat differently are one concept, with the variants in its definition. A scale of values (levels, modes) is one `Abstract` concept with its values in the definition and in `alt_labels`, unless the source gives a value its own behaviour.

For each concept:
- `id`: lowercase slug; `label`: the source's term; `alt_labels`: its other names.
- `definition`: 1–3 sentences on what it is **according to this source**, in English, from the text.
- `level`: `application`.
- `upper_category`: one of {categories}. Things with identity that persist → `Kind`; a specialization of another concept here → `SubKind` with `broader`; a stage a thing passes through → `Phase` with `broader`; a role played relative to others → `Role`; something that exists only by connecting others → `Relator`; something that happens or runs → `Event`; value types → `Abstract`.
- `inventory`: the ids of the inventory candidates above that this concept covers (empty when none).
- `evidence`: at least one `{{path, lines, kind}}`; `path` relative to the repository root, `lines` like `"40-88"`, `kind`: `doc`. Read the lines before citing them. When the source contradicts itself, set `status: "disputed"`, cite both and explain in `note`. Otherwise `status: "hypothesis"`.
- No `maps_to`.

{variation_block}

Relations between your concepts: `{{from, to, type, label, evidence}}`, `type` one of {relation_types}. Keep the source's own name of the relation in `label`.

**Absence claims.** When you state that the source lacks something, add it to `absences` with the search protocol: `{{"claim": "...", "protocol": {{"what": "terms and concepts searched for", "where": "paths", "how": "tools and queries used"}}, "result": "what was found"}}`. An absence without a protocol is not accepted.

## Output

Write `{output}`:

```json
{{
  "graph": "projects/{project}",
  "project": "{project}",
  "project_label": "<display name>",
  "source": {{"repo": "{project}", "commit": "{commit}"}},
  "extractor": {{"id": "{extractor_id}", "harness": "{harness}", "model": "{model}", "run_id": "<your session id if you know it, else {extractor_id}-{project}-{run}>"}},
  "summary": "<one paragraph: what the source is and what it claims>",
  "concepts": [], "relations": [], "variation": [], "proposed_values": [], "absences": [], "caveats": []
}}
```

Then check it, from `{owner_dir}`: `{validate_cmd}`. Fix every error and re-run until it prints `would change` or `unchanged`. Errors that only concern the ids you put in `proposed_values` are expected: the coordinator adds accepted values before ingest.

## Report back

Counts (concepts, relations, variation entries, absences, caveats), the inventory candidates you did not cover and why, and the validate output.
