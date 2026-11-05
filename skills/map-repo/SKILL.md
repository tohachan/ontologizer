---
name: map-repo
description: Use when a repository is to be mapped into the level-aware ontology (application concepts with mappings to shared domain/task concepts and variation values), or when several repositories are analysed for comparison. Produces extraction JSON, never RDF; writes only through the ontologizer write route.
---

# map-repo

> **Superseded.** A corpus is analysed by the staged process in `docs/process.md`: skills
> `frame-study` (stage 0) and `analyse-corpus` (stages 1–8). A single repository mapped onto an
> existing release is stage 9, the overlay run (`docs/process.md` §9, `analyse-corpus`). This
> file is the record of the first run's lessons.

Written from a first run of seven repositories (2026-11-04). The LLM never writes RDF:
agents emit JSON in the extraction schema (`src/ontologizer/core/extraction.py`),
deterministic code converts and validates it.

## Pipeline

Commands run from the owner's directory (a study or `<repo>/onto`) as
`uv run --project <kit> ontologizer …`; the paths below are the owner's `paths:`
(defaults shown), resolved against its `ontologizer.yaml`.

| Stage | Who | Command / artifact |
| --- | --- | --- |
| 0. Context | code | `ontologizer bootstrap`; shared levels `ontology/domain/`, `ontology/task/`, `ontology/variation/`; preset `presets/<preset>.yaml` of the kit |
| 1–2. Inventory + extraction | one agent per repository, in parallel | the study's brief (e.g. `docs/extraction-brief.md`) → `ontology/application/<repo>.json` |
| check | agent | `ontologizer validate ontology/application/<repo>.json` until `would change` |
| new variation values | coordinator | merge `proposed_values` into `variation/<scope>.json`, ingest it first |
| 5. Write | coordinator | `ontologizer ingest ontology/application/<repo>.json` |
| 3. Consolidation | coordinator (LLM + human) | `ontologizer consolidation-report` → decisions file `consolidation/<step>.yaml` → `ontologizer consolidate …` → ingest domain, task, then projects |
| 6. Report | code | `ontologizer export-md` → `exports/` |
| release | coordinator | `ontologizer release <shared domain graph> <ver>` (immutable) |

## Where files go

Nothing study- or project-specific lives in the kit. A comparison study is its
own repository with `ontologizer.yaml` (`hub:` → the kit, `shared:` → its
shared-level graphs), and analyses of the compared repositories belong to it.
A project mapping itself keeps its files in its own repo under `onto/`
(`onto/ontologizer.yaml`). Register both in the kit's `ontologizer.local.yaml`
so every side knows who owns which graph (see the kit's `AGENTS.md`).

## Rules learned in the first run

- Brief every agent with the same file; give it the shared seeds, the preset and
  the evidence source; forbid reading a project that will be overlaid on the
  shared levels later, so the shared levels are not built from it.
- Evidence: path + lines + commit. When code and docs disagree → `disputed`
  with both items; SHACL rejects a disputed claim with fewer than two.
- Shared concepts need ≥2 supporting projects; single-project candidates stay
  in the project graph.
- Expect domain-level candidates named after the project's own terms:
  consolidation, not SHACL, catches them.
- Agents mix up Kind/Relator for relationship-like entities (leases,
  assignments) and prefer `Abstract` over `Phase` for state enums; review these
  categories during `review`.
- Budget: ~160–230k tokens and 5–9 minutes per repository of 30–35 concepts.
- Never call `onto_load(path)`, `onto_plan`, `onto_apply`, `onto_rollback`, `onto_clear`
  on the shared store (`docs/open-ontologies.md`).

## Open

- Weighting primary vs secondary variation values.
- Relations within one project come out weaker than mappings (free labels,
  many `associatedWith`); a relation vocabulary per level may be needed.
- Large repositories were covered by subset (agents chose); a module-by-module
  split was not needed at 35 concepts.
