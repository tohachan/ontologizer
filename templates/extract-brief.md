# Extraction brief: {project} ({extractor_id}{part_label})

You extract the concepts of one software project into extraction JSON. You do not write RDF and you do not write to any store. This is extraction {extractor_id} of run `{run}`; at least one other model extracts the same project independently. Do not look for, read or wait for the other extraction.

## The study

{frame_summary}

Work in **the project's own terms**. There is no shared vocabulary to map to in this stage: name each concept as the project names it and define it as the project uses it. Mapping to shared concepts is a later stage.

Out of scope for the study: {out_of_scope}

{never_read}

## The source

- Repository `{project}` at commit `{commit}`, checked out at `{source_dir}`. Cite only this commit. Do not modify the checkout, run its code or install its dependencies.
- {inventory_line}

{inventory_block}

## What to extract

Concepts that carry the project's architecture: what it creates, stores, schedules or names in its API, CLI or UI, and the lifecycles and processes it runs. Prefer concepts that are types in code (structs, classes, tables, state enums, CLI nouns) and its main processes. Skip utilities, widgets and generic wrappers. {cap_line}

**Granularity.** One concept per architectural thing, not per code variant:
- a state enum is **one** `Abstract` concept; name its states in the definition and as `alt_labels`. Make a state its own `Phase` concept only when the code gives that state its own data or behaviour (a separate record, handler or timer);
- variants of one interface (one adapter per provider, one hook per event) are **one** concept; name the variants in the definition. Split them only when they differ architecturally (different storage, protocol or lifecycle);
- a process with steps is one `Event` concept; the steps go in its definition unless each is scheduled, retried or recorded on its own.

For each concept:
- `id`: lowercase slug; `label`: the project's term; `alt_labels`: other names in its code.
- `definition`: 1–3 sentences on what it is **in this project**, in English, from the source.
- `level`: `application`.
- `upper_category`: one of {categories}. Things with identity that persist → `Kind`; a specialization of another concept here → `SubKind` with `broader`; a stage a thing passes through → `Phase` with `broader` (a whole state enum is one `Abstract` concept plus `hasPhase` relations); a role played relative to others → `Role`; something that exists only by connecting others (assignment, claim, lease, membership) → `Relator`; something that happens or runs → `Event`; configuration-like value types → `Abstract`.
- `inventory`: the ids of the inventory candidates above that this concept covers (empty when none).
- `evidence`: at least one `{{path, lines, kind}}`; `path` relative to the repository root, `lines` like `"40-88"`, `kind` one of `code|doc|test|config`. Read the lines before citing them. When code and docs disagree, set `status: "disputed"`, cite both and explain in `note`. Otherwise `status: "hypothesis"`.
- No `maps_to`.

{variation_block}

Relations between your concepts: `{{from, to, type, label, evidence, inventory}}`, `type` one of {relation_types}; `inventory`: the property ids above that the relation states (empty when none).

**Absence claims.** When you state that the project lacks something, add it to `absences` with the search protocol: `{{"claim": "...", "protocol": {{"what": "terms and concepts searched for", "where": "paths, modules, docs", "how": "tools and queries used"}}, "result": "what was found"}}`. An absence without a protocol is not accepted.

## Output

Write `{output}`:

```json
{{
  "graph": "projects/{project}",
  "project": "{project}",
  "project_label": "<display name>",
  "source": {{"repo": "{project}", "url": "<repository url>", "commit": "{commit}"}},
  "extractor": {{"id": "{extractor_id}", "harness": "{harness}", "model": "{model}", "run_id": "<your session id if you know it, else {extractor_id}-{project}-{run}>"}},
  "summary": "<one paragraph: what the project is and its architectural style>",
  "concepts": [], "relations": [], "variation": [], "proposed_values": [], "absences": [], "caveats": []
}}
```

Then check it, from `{owner_dir}`: `{validate_cmd}`. Fix every error and re-run until it prints `would change` or `unchanged`. Errors that only concern the ids you put in `proposed_values` are expected: the coordinator adds accepted values before ingest.

## Report back

Counts (concepts, relations, variation entries, absences), the inventory candidates you judged architectural but did not cover and why, caveats, and the validate output.
