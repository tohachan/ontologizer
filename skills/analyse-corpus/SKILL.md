---
name: analyse-corpus
description: Use when a corpus of software projects is analysed into a level-aware ontology with measured reliability — stages 1–8 of the kit's analysis process (inventory, double extraction, reconciliation, mapping, consolidation, verification, stability and report, owner review and calibration), after the frame is approved; also when two releases are compared, an ablation is run, or the overlay target is mapped onto a release (stage 9). Covers dispatching the extraction agents, answering llm-mode decisions, cross-harness runs and what each gate needs.
---

# analyse-corpus (stages 1–8)

The process and its metrics are in the kit's `docs/process.md`. This skill is the operator's side: which command to run, which agent to dispatch, and what to write between commands. Commands run from the owner repository as `uv run --project <kit> ontologizer … [--run ID]`. The built-in adapters (`graphify` inventory, `jev` decisions, `checkout` evidence) need nothing extra; an adapter from another package needs `--with <package>` (README §Adapters).

Agents produce JSON (extractions, resolutions, answers), never RDF. Writes to the store go only through `ingest`, `import-graph` and `release`.

## Before stage 1

- The frame is approved: `ontologizer frame check` prints `approved by …`. If it does not, run the skill `frame-study`.
- The config has `process:` (`run`, `extractors`, optional `max_concepts`), `inventory:`, `evidence:` and `decisions:` (`adapter`, `default`, `stages`, `thresholds`). A new run gets a new id. Never overwrite an earlier run's directory.

## Stages

| # | Do | Gate |
| --- | --- | --- |
| 0→1 | `ontologizer frame variation` writes the frame's dimensions into the shared variation file; ingest it first. | — |
| 1 | `ontologizer inventory` (all corpus projects; `--rebuild-stale` when the adapter can rebuild). Triage runs in `llm` mode: answer `decisions/inventory.pending.json` with the model `process.roles.inventory` names (Codex via `codex exec`), then re-run `inventory`. | every project `measured` or `unmeasured` with a reason |
| 2 | For each project and extractor, `ontologizer brief extract <p> --extractor <id>` (add `--parts N` for a large project). Dispatch one agent per brief, all in parallel. Each agent runs on the extractor's model and gets the brief path as its whole task. Codex extractors run with the `codex exec` command the brief prints, or through a session launcher (`docs/cross-harness.md`). An extractor with `min_candidates: N` is required on every project whose inventory lists at least N candidates. | every `extract/<p>/<id>.json` validates (`ontologizer validate <file>`); ≥2 extractors per project, and every required one |
| 3 | `ontologizer reconcile <p>`. Dispatch a Sonnet agent with `reconcile/<p>.json`. It reads the cited source lines for each dispute and writes `reconcile/<p>.resolutions.json`, as the file's `how_to_resolve` describes. Every resolution cites the lines it read (`source: [{path, lines, quote}]`, an exact short quote); `--apply` checks the quote. Then `reconcile <p> --apply`, `ingest application/<p>.json --dry-run`, `ingest`. | every dispute resolved with a reason and a quote found in the source; dry run passes |
| 4 | `ontologizer map <p>`. Dispatch an Opus agent (the model `process.roles.map` names) with `map/<p>.json` and the shared-level files. It writes `map/<p>.answers.json` (`how_to_answer`); a `partial` mapping needs `overlap` and `differs`, else it is none. Then `map <p> --apply`. | every concept has a decision; every partial names its overlap and difference; every disputed category is decided with a reason |
| 5 | `ontologizer group`. Dispatch an **Opus** agent with `consolidate/groups.json` and the frame. It writes `consolidation/<run>.yaml` in the `consolidate` format: `model:` at the top, and for each new concept `id, label, cat, def, support, reason` (optional `alt`, `candidates`, `maps`, `attach_unmapped`). It also lists each eligible group it rejects under `rejected:` with a reason. Then `ontologizer consolidate consolidation/<run>.yaml`. Ingest the shared levels, then the projects. | `consolidate` accepts the file: ≥2 families and a reason per concept, no overlay support |
| 6 | `ontologizer verify <p>`. Run the recheck with the default rechecker it prints (`process.recheck`, Codex via `codex exec`). Only when that harness is unavailable, use the declared fallback and say why in `fallback_reason`. It writes `verify/<p>.recheck.answers.json`. Then `verify <p> --apply` and ingest again. | every claim has a verdict; every recheck item has a verdict and a reason; absences carry protocols |
| 7 | `ontologizer stability`, then `ontologizer report`. For competency questions without a query, write `runs/<run>/cq.yaml` (`<id>: {status: answered\|partial\|unanswered, by: [concept refs], note}`) before the report. A question tagged `target:` also gets `projects: {<project>: full\|partial\|none}`, judged from that project's concepts and evidence, and `by` lists the concepts (`<project>:<id>` or shared refs) that realize the requirement: these are the parts to reuse. The report turns them into the target's fit matrix. | report generated |
| 8 | `ontologizer review build`; dispatch a strong agent (Opus 5.5, effort high, or equivalent; not an author of the judgements) with `review.yaml`: it checks each concept in the source and fills the verdicts; `ontologizer review apply --by <its model> --effort <effort>`. Then `ontologizer gold sample <name>`; dispatch a strong agent that made none of the logged decisions to label it and set `labelled_by: {model, effort, harness}`; `ontologizer gold calibrate gold/<name>.yaml`. People do not review these volumes. Only the owner releases the version as one set: `ontologizer release <shared graphs> <project graphs> <version>` (references inside the set move to the version's IRIs together). | review and gold gates recorded with the agent; `release` refuses without the review |

Re-running a stage command is safe: answered decisions are cached in `decisions.jsonl`, and Jev is not paid twice for the same question.

## Answering decisions (llm mode)

When a stage runs in `llm` mode (or exits with code 4), it has written `decisions/<stage>.pending.json`. Dispatch an agent with that file. In stage 6, the agent's model must differ from the extraction models. The agent writes `decisions/<stage>.answers.json`: `{"model": "<id>", "answers": {"<key>": {"value": …, "confidence": 0..1, "reason": "…"}}}`. Then re-run the stage. Answer from the `state` given, not from memory. A question that cannot be answered gets `{"error": "refused"}`, never a guess.

## Ablation (Jev against no Jev)

Run a stage in a second run directory with its mode set to `llm` or `off`: declare the run's modes under `decisions.runs.<run>.stages` (the main run's modes stay as they are), copy the inputs the stage needs (`extract/` for stage 3; stage 1 is re-run from the inventory source), and copy `cq.yaml` so the stages you did not ablate report as before. Run the stage with `--run <run-b>` and **never `--apply`**: the application files belong to the main run. Then `ontologizer report --run <run-b>` and `ontologizer report --compare <run-a> <run-b>`. The comparison lists the stage 3 pairs that only one run matched; label a sample of them with a model that made none of the judgements to see which side is right. The main run's report carries the decision cost per stage.

## Comparing releases and the overlay (stages 7 and 9)

- `ontologizer release-compare <old> <new>` from the study, after `<new>` is released: kept, renamed, split, merged, added and dropped concepts with reasons. Read the dropped ones whose nearest candidate sits just below the threshold: they are the owner's call.
- The overlay target's owner runs stage 9 in its own config (`docs/process.md` §9): keep its earlier application file (`git show`), then `brief extract`, the two extractions, `reconcile`, `map`, `verify` and their `--apply` as above, `ingest`, and `ontologizer overlay --previous <earlier file> --compare <release-compare JSON>`.

## Rules

- The overlay target named by the frame is never read in stages 1–8. The commands refuse it, and briefs and dispatches must not mention its files.
- Do not show one extractor's output to another. Agreement is only meaningful between independent extractions.
- No cap on concepts unless `process.max_concepts` is set for a pilot.
- Every claim stays a hypothesis until stage 8. Only `review apply` makes a shared concept `asserted`, on the verdicts of a strong agent that made none of the judgements.
- Never call `onto_load(path)`, `onto_plan`, `onto_apply`, `onto_rollback`, `onto_clear` on the shared store.
