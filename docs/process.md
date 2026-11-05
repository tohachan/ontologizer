# Analysis process: stages 0–9

How an owner repository turns a corpus of software projects into a level-aware ontology with measured reliability. The process does not depend on the topic of the corpus. Everything topic-specific comes from the frame (stage 0) and the owner's config.

The process replaces a single pass in which seeds anchored the agents, coverage was the agent's choice, each project was extracted once, consolidation grouped by words and nothing was checked independently. Each stage below has an input, an output, the party that does the work, and a gate that must hold before the next stage starts. Each stage writes its result into a run directory, so two runs can be compared mechanically.

## Principles

1. **Code where code suffices.** Matching, grouping, counting, sampling and validation are deterministic code. Judgements go to models, and each judgement is recorded with its inputs, its output and who made it.
2. **Independence over volume.** Two extractions by different models are better than one long extraction. A verification is made by a different model than the claim it checks.
3. **Narrow decisions are separate from open judgements.** A narrow decision has a closed answer: one of several options, a score on a scale, a yes/no probability. It goes through the decision port and can be answered by Jev, by an LLM, or skipped. An open judgement (a definition, a resolution, a mapping kind with its reason) goes to an LLM agent through a brief.
4. **Every Jev stage runs without Jev.** Each decision stage has the switch `jev | llm | off`. The run report states which mode each stage ran in, so the value of Jev is measured by turning it off.
5. **Absence of a fact is a claim too.** "Project X has no Y" needs a recorded search protocol, not a failed search.
6. **Hypotheses until review.** Nothing becomes `asserted` without the stage 8 review. The reviewer is a strong agent (Opus 5.5 at high effort or equivalent) that made none of the judgements under review; people do not review these volumes. The owner's word is required for the frame (stage 0) and for a release.
7. **The overlay target is never read while the shared levels are built.** The frame names it; stages 1–8 refuse to read it.

## Run directory

A run is one pass of stages 1–8 over the corpus. Its files live in the owner repository under `process.runs` (default `runs/`):

```
frame.md                                   stage 0, owner-approved
gates.yaml                                 recorded gates (frame approval, review)
runs/<run>/
  run.yaml                                 run id, modes per stage, extractors, harnesses available
  decisions.jsonl                          every narrow decision: stage, mode, question, answer, model, time
  decisions/<stage>.pending.json           questions waiting for an LLM agent (llm mode)
  decisions/<stage>.answers.json           the agent's answers, read back by the stage
  inventory/<project>.json                 stage 1
  extract/<project>/<extractor>.json       stage 2, one file per independent extraction
  reconcile/<project>.json                 stage 3: pairs, agreement, disputes
  reconcile/<project>.resolutions.json     stage 3: the LLM's resolution of each dispute, with reasons
  map/<project>.json                       stage 4: candidates, category second opinion
  map/<project>.answers.json               stage 4: the LLM's mapping decisions
  consolidate/groups.json                  stage 5: meaning groups and their family support
  verify/<project>.json                    stage 6: per-claim verdicts, rechecks, absence protocols
  stability.json                           stage 7
  report.json, report.md                   stage 7
  review.yaml                              stage 8: the reviewing agent's verdict per shared concept
ontology/application/<project>.json        the reconciled, mapped extraction; what is ingested
ontology/domain/, ontology/task/           the shared levels (stage 5 writes them)
consolidation/<run>.yaml                   stage 5 decisions (existing `consolidate` format plus reasons)
gold/<name>.yaml                           stage 8 gold set, labelled by a strong agent (`labelled_by`)
calibration.yaml                           stage 8 thresholds with measured accuracy
```

The run directory is the record. The store holds only what passed through the write route: the reconciled extractions, the shared levels and the releases.

## Stages

| # | Stage | Input | Output | Who | Gate before the next stage |
| --- | --- | --- | --- | --- | --- |
| 0 | Frame | the owner's question, the corpus | `frame.md` | research agent (strong model) writes; owner approves | `ontologizer frame check` passes; owner's approval recorded in `gates.yaml` with the frame's hash |
| 1 | Inventory | source graphs (inventory adapter) | `inventory/<p>.json` | code; triage by decisions | file exists for every project, or the project is marked `unmeasured` |
| 2 | Double extraction | frame, inventory, source | `extract/<p>/<e>.json` | ≥2 LLMs independently (Opus, Sonnet; optional Codex) | every extraction validates; at least two extractors per project, or the missing one recorded |
| 3 | Reconciliation | the extractions of a project | `reconcile/<p>.json`, `ontology/application/<p>.json` | code pairs; decisions judge pairs; LLM resolves disputes | the reconciled extraction validates and passes the write route dry run |
| 4 | Mapping | reconciled extraction, shared levels | `map/<p>.json`, `maps_to` in `ontology/application/<p>.json` | decisions propose targets and a category second opinion; LLM decides the mapping kind | every concept has a mapping decision (a target or `none`) |
| 5 | Consolidation | unmapped concepts of all projects, frame families | `consolidate/groups.json`, `consolidation/<run>.yaml`, shared levels | decisions judge same-concept pairs; code groups; strong LLM (Opus) decides | every new shared concept has support from ≥2 families and a recorded reason |
| 6 | Verification | every claim of the run | `verify/<p>.json` | decisions or a different LLM check each evidence item; a different model rechecks disputed judgements | every claim has a verdict; every absence claim has a protocol |
| 7 | Stability and report | shared levels, support, all stage outputs | `stability.json`, `report.json`, `report.md` | code | report generated |
| 8 | Review and calibration | report, shared concepts | `review.yaml`, `gold/*.yaml`, `calibration.yaml` | a strong agent (review, gold labels); the owner releases | review and gold gates recorded in `gates.yaml` with the agent's model and effort; only then the owner's `release` of the shared levels and the projects as one version |
| 9 | Overlay | a released shared level, the overlay target | the target's application layer and verdict | stages 2–4 and 6 on the overlay target | — |

### 0. Frame

A research agent writes `frame.md` from the template `templates/frame.md`. The frame fixes:
- the domain or domains and their boundaries;
- existing ontologies and standards to reuse, with what is taken from each;
- the comparison corpus, grouped into **architectural families**: projects in one family share an architecture, so their agreement counts once;
- competency questions: what the finished ontology must answer, each tagged with a level and, when it can be answered mechanically, a SPARQL query;
- the levels preset;
- the overlay target, which the shared levels must not be built from;
- what is out of scope;
- optionally, the **variation dimensions**: the decisions projects differ on, each with its values. Every extraction reports the values its project takes, with evidence. `ontologizer frame variation` writes them into the shared variation level;
- optionally, **design targets**: a system the study should help build or choose, described as requirements. Each requirement is a competency question tagged `target: <id>`. The run report answers it per project (`full`, `partial`, `none`), names the concepts that realize it — the parts to reuse — and ranks the projects by fit.

`ontologizer frame check` validates the required sections and the machine-readable header. `ontologizer gate approve frame --by <name>` records the owner's approval with the frame's sha256. Stage 1 refuses to start when the approval is missing or the frame has changed since.

### 1. Inventory

The inventory adapter (e.g. the built-in `graphify`) lists the candidates of each project deterministically: types, enums, tables, public interfaces, CLI and API nouns, each with its source location and its module or community. Coverage is then measured against this list instead of against the extractor's own choice.

With decisions on, each candidate is triaged into *architectural concept / implementation detail / UI / test / config*, and only architectural concepts count for coverage. With decisions off, every candidate counts. Triage runs in `llm` mode with the model `process.roles.inventory` (a model stronger than Jev, from another vendor than the gold labeller). Jev's triage probabilities separate architectural candidates too weakly to bound their loss, so `jev` mode is kept for ablation, not for coverage.

Without a graph for a project, the stage records the project as `unmeasured`. Coverage is then reported as unmeasured, never as zero.

### 2. Double extraction

Each project is extracted at least twice, independently, by different models: Opus and Sonnet by default, Codex with an OpenAI model as an optional third (see [cross-harness runs](cross-harness.md)). The brief is generated by `ontologizer brief extract <project> --extractor <id>`. It carries the frame and the inventory and works in the project's own terms: there is no shared vocabulary in it, so earlier results cannot anchor the extraction. Large projects are split by module; the brief lists the modules from the inventory.

The brief also lists the frame's variation dimensions. The extraction records each value the project takes, with evidence, and proposes a new value when none fits.

Extraction no longer maps to the shared levels: `maps_to` moves to stage 4. Old extraction files with `maps_to` still load. Each concept may cite the inventory candidates it covers (`inventory: [ids]`). There is no cap on the number of concepts unless the owner sets `process.max_concepts` for a pilot.

### 3. Reconciliation

Code proposes candidate pairs between the two extractions from shared inventory references, shared evidence paths and label similarity. The decision port asks of each candidate pair whether both are the same concept (`noul`). Code then matches pairs one-to-one above the threshold.

**Extraction agreement** is the share of matched concepts in the union of both extractions. Unmatched concepts and matched pairs that disagree on category or definition are disputes. An LLM resolves each dispute into a single reconciled extraction and records its reason and the source lines it read: `source: [{path, lines, quote}]`. `reconcile --apply` refuses a resolution whose quote is not at those lines of the analysed commit (`process.resolution_sources: false` only for runs made before the rule). A project whose inventory lists at least an extractor's `min_candidates` needs that extractor too (a third, cross-vendor extraction on large projects); with three or more extractors the report adds **agreement across all extractors**, the share of clusters every extractor found. The reconciled extraction is written to `application/<p>.json`, validated, and ingested through the write route.

### 4. Mapping

For each reconciled concept the decision port chooses candidate targets among the shared concepts of the matching level, plus `none`. An LLM then decides the mapping: the target and its kind (`equivalence`, `specialization`, `roleOf`, `realization`, `partial`), or none. `partial` is the last resort: it must state `overlap` (what both cover) and `differs` (what one has that the other does not), or `map --apply` refuses it, and every partial mapping is rechecked in stage 6. In llm mode the LLM also proposes the candidates.

The gUFO category of each concept gets a second opinion from the decision port. Where it differs from the extraction's category, the concept goes to the review list. The **disagreement rate** is reported.

### 5. Consolidation

Concepts without a mapping are grouped by meaning. Code proposes candidate pairs across projects. The decision port judges whether each pair is the same concept, and code builds the groups from the accepted pairs. A strong model (Opus) then decides which groups become shared concepts.

A shared concept needs support from **at least two architectural families** of the frame. Two projects from one family count once. The decision, the supporting projects, the families and the reason are recorded per concept. Consolidation never reads the overlay target.

### 6. Verification

Every evidence claim is checked: the cited lines are read from the source at the cited commit, and the decision port (or, in llm mode, a model other than the extractor) judges whether they support the claim. The **verified-evidence share** is reported. A claim whose evidence does not hold becomes `disputed` or is removed.

Low-confidence and disputed judgements (mapping kind, category) and every `partial` mapping are rechecked by a model other than the one that made them. The default rechecker is `process.recheck` (another vendor, e.g. `{harness: codex, model: …}`); its `fallback` (e.g. the other Claude model) may answer only with a `fallback_reason`, and `verify --apply` refuses any other rechecker. The report counts rechecks by policy and gives the **recheck change rate** per item type, including `mapping-partial`.

An **absence claim** ("the project has no X") requires a recorded protocol: what was searched for, where, and how (queries, paths, tools), and the result.

### 7. Stability and run report

Leave-one-out: for each project, the support of every shared concept is recomputed without that project. A concept that keeps support from at least two families under every removal is `stable`. One that drops below with some project removed is `fragile` and names that project. One without enough support even with all projects is `unsupported`.

`ontologizer report` collects all metrics below (competency questions with a query run through the engine's `onto_cq_run`; consistency through `onto_reason` as a dry run) and the answer status of every competency question into `report.json` and `report.md`. For each design target it adds a fit matrix: requirements × projects, the concepts to reuse per requirement, and the projects ranked by fit (full = 1, partial = ½, averaged over the assessed requirements). `ontologizer report --compare A B` compares two runs mechanically; when both runs have stage 3 files, it also lists the matched pairs per project that are in both runs, only in A and only in B (what an ablation changed).

**Release comparison.** Two releases built by independent runs name the same concept differently, so ids do not say what was kept. `ontologizer release-compare A B` (from the owner of the shared levels, `--run` the run that built B) reads both releases from the store, proposes candidate pairs across them (TF-IDF top 3 each way, plus equal labels) and has the decision port judge them in batches of 20 pairs (stage `compare`, threshold `compare_same_concept`, by default the grouping one). The accepted pairs give each concept a kind: `kept` (same id), `renamed`, `split`, `merged`, `regrouped`, `dropped` or `added`. An added concept carries its consolidation reason and its review verdict. A dropped one is traced through run B's project concepts: `absorbed` (its counterparts map to B concepts), `rejected` (consolidation or review rejected their group), `one-family`, `project-only`, or `no-run-counterpart`. Both sides carry the nearest candidate below the threshold, release metrics (concepts per level, status, evidence per concept) and the run metrics of B, and of A with `--run-a`. The result is `runs/<B run>/release-compare-A-B.{md,json}`.

### 8. Review and calibration

`ontologizer review build` writes `review.yaml`. It lists every domain and task concept, with no cap, together with its support, families, stability, verification share and evidence. The reviewing agent checks each one in the source and marks it `accept`, `reject` or `revise`. `ontologizer review apply --by <model> --effort <effort>` makes the accepted ones `asserted` in the shared-level files and records the review gate with the reviewer. Only after that does the owner run `release`.

`ontologizer gold sample` draws a gold set: 80 items by default (`process.gold_size`), mixing evidence claims, categories, same-concept pairs (stages 3, 5 and `compare`) and triage classes. A strong agent that made none of the logged decisions labels it and records itself as `labelled_by: {model, effort, harness}`. `ontologizer gold calibrate` refuses a gold set without `labelled_by`, or labelled by a model that made any of its decisions. It compares the decisions with the labels and chooses each threshold for accuracy. `triage_keep` is chosen among the thresholds that lose at most `decisions.triage_max_loss` (default 0.03) of the architectural candidates. The thresholds and the measured accuracy are written to `calibration.yaml`, and the gold gate is recorded with the labeller. A kind with fewer than 20 items stays provisional and is reported, not applied, unless `process.apply_provisional` says so.

### 9. Overlay

The overlay target maps itself onto a released shared level: stages 2–4 and 6 run on the target alone, and nothing is consolidated from it. The result is the target's application layer and its verdict: what maps, what has no counterpart, and which shared concepts it lacks.

The run lives in the target owner's config:

- `process.overlay: true` and `process.frame` pointing at the study's frame (approved in the `gates.yaml` beside it). The stage commands then accept only the frame's overlay target, and `group`, `stability`, `review` and `gold` refuse to run.
- `shared:` and `validation_context` name the release graphs (`<level>@<ver>`): mappings point at the release's terms, and stage 4 reads the shared concepts from the store, since the target has no shared-level files. `map/<p>.json` then carries them for the answering agent.
- The extraction brief forbids the target's earlier analysis (its owner directory), so earlier results do not anchor the extraction.

`ontologizer overlay` writes `runs/<run>/overlay.{md,json}`: for each shared concept the target concepts that realize it and how, the concepts without a counterpart and the shared concepts the target lacks. With `--previous <earlier application file> --compare <release-compare JSON>` it also lists, per shared concept of the new release, whether the target realized it before (through the correspondence of the releases) and now: `still realized`, `newly realized`, `no longer realized`, `still lacking`, or `new concept, realized|lacking`, and the earlier mappings whose target was dropped.

## Model roles

| Role | Model | Why |
| --- | --- | --- |
| Frame research | Opus | open-ended research and judgement |
| Extraction A / B | Opus / Sonnet, independently | two model families of different size disagree in useful ways |
| Extraction C | Codex with an OpenAI model (`codex exec` or a session launcher); required on a project whose inventory lists at least `min_candidates` (the extractor's config) | a different vendor and harness; two-model agreement caps the project layer |
| Narrow decisions (pairs, mapping candidates, category second opinion, evidence) | Jev (`typesafe/jev-1.13` via OpenRouter) | cheap, independent probabilities; thresholds calibrated on an agent-labelled gold set |
| Inventory triage | `process.roles.inventory`, `llm` mode (Codex) | Jev's triage signal is too weak to bound the loss of architectural candidates |
| Dispute resolution | Sonnet | bounded judgement with reasons; a resolution cites the source lines it read |
| Mapping decisions (stage 4) | Opus, `process.roles.map`: `map --apply` refuses answers by another model | `partial` mappings by a weaker model fail the cross-vendor recheck far more often; `partial` must name its overlap and its difference |
| Consolidation | Opus | strongest judgement on the most consequential step |
| Verification, recheck (stages 4 and 6) | the default rechecker `process.recheck` (another vendor: Codex), else its declared `fallback` with a `fallback_reason`; never the claim's author | independence; the run report counts rechecks by policy and the change rate by item type |
| Review, gold labels | a strong agent: Opus 5.5 at high effort or equivalent, not the author of the judgements it reviews or labels | people do not review these volumes; the gates record the agent |
| Frame approval, release | the owner | the owner's decisions about scope and publication |

The extraction model ids are config (`process.extractors`), and the model that answers a stage's LLM decisions is pinned by `process.roles.<stage>` (`map`, `inventory`, …), so a run can change them without changing the process.

### Decisions without Jev

Each stage that asks narrow questions has its own switch under `decisions.stages`:

| Mode | What answers | Recorded as |
| --- | --- | --- |
| `jev` | the Jev adapter, synchronously | `mode: jev`, the probabilities and the confidence |
| `llm` | an LLM agent: the stage writes `decisions/<stage>.pending.json`, the agent answers in `decisions/<stage>.answers.json`, the stage is re-run | `mode: llm`, the model, the answer and its stated confidence |
| `off` | nothing; the stage falls back to its deterministic path (inventory: keep every candidate; pairs: label equality) | `mode: off` |

Thresholds live in config (`decisions.thresholds`) and are provisional until calibrated in stage 8. A run can override the modes for itself, which is how an ablation run is declared: `decisions: {runs: {r2-nojev: {stages: {reconcile: off}}}}`.

## Metrics

Every metric is computed by code from the run directory and is written to `report.json` per project and overall.

| Metric | Definition | Stage |
| --- | --- | --- |
| Inventory coverage | inventory candidates counted for coverage (architectural ones when triage is on) that a reconciled concept cites ÷ all such candidates; `unmeasured` without an inventory | 1, 3 |
| Extraction agreement | matched concepts ÷ (concepts of A + concepts of B − matched), per project pair of extractors | 3 |
| Jev–LLM disagreement rate | questions answered by both Jev and an LLM (the category second opinion; the mapping candidate against the chosen target) where the answers differ ÷ such questions | 4, 6 |
| Verified-evidence share | evidence items judged supporting ÷ evidence items checked; unchecked items are counted separately | 6 |
| Leave-one-out stability | shared concepts marked `stable` ÷ shared concepts | 7 |
| Competency-question coverage | questions answered ÷ questions; `partial` and `unanswered` are listed by id | 7 |
| Consistency clashes | contradictions the engine's reasoner finds over the store (`onto_reason`, RDFS, dry run); releases have their own IRIs, so a clash lies inside one version | 7 |
| Target fit (per target, per project) | mean over the target's assessed requirements of full = 1, partial = ½, none = 0 | 7 |

The report also records, per stage, the decision mode and the number of decisions, and for every extraction its harness, model and run id. When no cross-harness result is present, the report says that the run used Claude models only.

## Commands

| Stage | Command |
| --- | --- |
| 0 | `ontologizer frame init`, `ontologizer frame check`, `ontologizer frame variation`, `ontologizer gate approve frame --by NAME` |
| 1 | `ontologizer inventory [PROJECT…]` |
| 2 | `ontologizer brief extract PROJECT --extractor ID`, then the agent writes `extract/<p>/<id>.json`; `ontologizer validate` checks it |
| 3 | `ontologizer reconcile PROJECT` (pairs and disputes), `ontologizer reconcile PROJECT --apply` |
| 4 | `ontologizer map PROJECT`, `ontologizer map PROJECT --apply` |
| 5 | `ontologizer group`, then `consolidate consolidation/<run>.yaml` |
| 6 | `ontologizer verify PROJECT`, `ontologizer verify PROJECT --apply` |
| 7 | `ontologizer stability`, `ontologizer report`, `ontologizer report --compare A B`, `ontologizer release-compare A B [--run-a ID]` |
| 8 | `ontologizer review build`, `ontologizer review apply`, `ontologizer gold sample`, `ontologizer gold calibrate` |
| 9 | in the target owner's overlay run: `brief`, `reconcile`, `map`, `verify` on the target, then `ontologizer overlay [--previous FILE --compare FILE]` |

Each command accepts `--run ID` (default `process.run` in the config). The agent-side steps are in the skill [`analyse-corpus`](../skills/analyse-corpus/SKILL.md).
