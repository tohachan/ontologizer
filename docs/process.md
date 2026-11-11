# Analysis process: stages 0–10

How an owner repository turns a corpus of software projects, or whatever sources exist on a topic, into a level-aware ontology with measured reliability. The process does not depend on the topic of the corpus. Everything topic-specific comes from the frame (stage 0) and the owner's config.

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
  verify/<project>.json                    stage 6: per-claim verdicts, rechecks, absence protocols, removed claims
  verify/<project>.owner.json              stage 6: the owner's own evidence verdicts, when any
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
| 5 | Consolidation | unmapped concepts of all projects, frame families | `consolidate/groups.json`, `consolidation/<run>.yaml`, shared levels | decisions judge same-concept pairs; code groups; strong LLM (Opus) decides | every new shared concept has support from ≥2 families (or the frame's `admission_families`) and a recorded reason |
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
- optionally, `admission_families`, when the ontology grows from whatever sources exist instead of a comparison corpus: the families a concept needs to enter the shared levels (see §5);
- optionally, `sources`: per project, `kind: description` for a text about a subject rather than a system's own source (see §2), with its `origin`, and `derived_from` when it retells another source of the corpus: it must then sit in that source's family;
- optionally, `constraints: {scope}`: the study also extracts **constraints**, of the kind `scope` says (for example "the invariants and promise boundaries the project states as such"). See [Constraints](#constraints). Without the key nothing is asked and nothing changes;
- optionally, **design targets**: a system the study should help build or choose, described as requirements. Each requirement is a competency question tagged `target: <id>`. The run report answers it per project (`full`, `partial`, `none`), names the concepts that realize it — the parts to reuse — and ranks the projects by fit.

The frame's `study` repeats the config's `project`: the name comes from there, since graphs and IRIs follow it, and is chosen when the topic is created, never taken from a source.

`ontologizer frame check` validates the required sections and the machine-readable header. `ontologizer gate approve frame --by <name>` records the owner's approval with the frame's sha256. Stage 1 refuses to start when the approval is missing or the frame has changed since.

### 1. Inventory

The inventory adapter (e.g. the built-in `graphify`) lists the candidates of each project deterministically: types, enums, tables, public interfaces, CLI and API nouns, each with its source location and its module or community. Coverage is then measured against this list instead of against the extractor's own choice.

With decisions on, each candidate is triaged into *architectural concept / implementation detail / UI / test / config*, and only architectural concepts count for coverage. With decisions off, every candidate counts. Triage runs in `llm` mode with the model `process.roles.inventory` (a model stronger than Jev, from another vendor than the gold labeller). Jev's triage probabilities separate architectural candidates too weakly to bound their loss, so `jev` mode is kept for ablation, not for coverage.

For an RDF source, the `rdf` adapter lists every named term by kind: classes (`type`) and SKOS concepts (`concept`), which concepts cover, and properties, SHACL shapes, individuals of the file's own classes, concept schemes and the ontology header. An extraction accounts for those in the `inventory` of what carries them: a property in its relation, a shape in the caveat that states its rule, an individual in the caveat that reports it as an example or in the concept it is an instance of (caveats then take the form `{text, evidence, inventory}`). The brief lists them apart, and the report counts coverage per kind; the headline coverage stays over the kinds concepts cover, so it compares with earlier runs. `kinds:` in the adapter's config lists fewer.

Without a graph for a project, the stage records the project as `unmeasured`. Coverage is then reported as unmeasured, never as zero.

### 2. Double extraction

Each project is extracted at least twice, independently, by different models: Opus and Sonnet by default, Codex with an OpenAI model as an optional third (see [cross-harness runs](cross-harness.md)). The brief is generated by `ontologizer brief extract <project> --extractor <id>`. It carries the frame and the inventory and works in the project's own terms: there is no shared vocabulary in it, so earlier results cannot anchor the extraction. Large projects are split by module; the brief lists the modules from the inventory.

The brief also lists the frame's variation dimensions. The extraction records each value the project takes, with evidence, and proposes a new value when none fits.

When the frame has `constraints:`, the brief asks for them too ([Constraints](#constraints)); they go into `constraints`, not `caveats`. A project split into parts keeps them, their concept ids renamed with the concepts'.

A source with `kind: description` gets its own brief. What it says is its author's claim, its evidence is `kind: doc`, and what extraction JSON cannot carry (rules, limits, recommendations, sample systems) goes into `caveats`, not into definitions. An adapter or a converter is not needed for such a source to be cited: it only has to be text under git, either its own repository or a directory committed inside a larger one, such as the owner's. The built-in `rdf` inventory adapter lists the classes and SKOS concepts of an RDF source, so coverage is measured there too.

Extraction no longer maps to the shared levels: `maps_to` moves to stage 4. Old extraction files with `maps_to` still load. Each concept may cite the inventory candidates it covers (`inventory: [ids]`). There is no cap on the number of concepts unless the owner sets `process.max_concepts` for a pilot.

### 3. Reconciliation

Code proposes candidate pairs between the two extractions from shared inventory references, shared evidence paths and label similarity. The decision port asks of each candidate pair whether both are the same concept (`noul`). Code then matches pairs one-to-one above the threshold.

**Extraction agreement** is the share of matched concepts in the union of both extractions. Unmatched concepts and matched pairs that disagree on category or definition are disputes. An LLM resolves each dispute into a single reconciled extraction and records its reason and the source lines it read: `source: [{path, lines, quote}]`. `reconcile --apply` refuses a resolution whose quote is not at those lines of the analysed commit (`process.resolution_sources: false` only for runs made before the rule). A project whose inventory lists at least an extractor's `min_candidates` needs that extractor too (a third, cross-vendor extraction on large projects); with three or more extractors the report adds **agreement across all extractors**, the share of clusters every extractor found. The reconciled extraction is written to `application/<p>.json`, validated, and ingested through the write route.

Constraints are kept from every extractor, as relations are, their concept ids remapped to the reconciled concepts. Two extractors' constraints with the same modality, the same constrained concepts and the same cited lines are one, kept once: the share of such pairs is the **constraints' agreement**, recorded in `reconciled.constraints`. The match is mechanical on purpose; a looser one is a decision to calibrate first. A term whose concept did not survive is unmarked, and a constraint left constraining nothing is dropped and listed.

### 4. Mapping

For each reconciled concept the decision port chooses candidate targets among the shared concepts of the matching level, plus `none`. An LLM then decides the mapping: the target and its kind (`equivalence`, `specialization`, `roleOf`, `realization`, `partial`), or none. `partial` is the last resort: it must state `overlap` (what both cover) and `differs` (what one has that the other does not), or `map --apply` refuses it, and every partial mapping is rechecked in stage 6. In llm mode the LLM also proposes the candidates.

The gUFO category of each concept gets a second opinion from the decision port. Where it differs from the extraction's category, the concept goes to the review list. The **disagreement rate** is reported.

### 5. Consolidation

Concepts without a mapping are grouped by meaning. Code proposes candidate pairs across projects. The decision port judges whether each pair is the same concept, and code builds the groups from the accepted pairs. A strong model (Opus) then decides which groups become shared concepts.

A shared concept needs support from **at least two architectural families** of the frame. Two projects from one family count once.

Two things are kept apart: *admission* (may a concept enter the shared levels) and *confirmation* (do independent families stand behind it, the preset's `domain_confirmation_min_projects`). By default they are the same number. A frame can lower admission (`admission_families: 1`) when there may be only one source. Then a concept without confirmation is marked `unconfirmed` in stage 7, and an `accept` in stage 8 leaves it a `hypothesis`. `ontologizer group` counts the groups in these terms: admissible (at least `admission_families`) and, when it differs, confirmed (at least the confirmation number); when the frame has fewer families than confirmation needs, it says that no group can be confirmed. Every concept of the source is then a candidate, including those in `singletons`, and the Opus agent still decides which to take and why. Families count origins: a retelling of a source belongs to the source's family.

A decision file adds to the decisions before it, so a later run lists only what changes:
- a concept named again replaces its earlier form, and a concept decided on the other level moves there, with the mappings to it;
- `removed: [{ref, reason, replaced_by}]` takes a concept out, with its relations and the mappings to it;
- `relations` join the existing ones, on the level of their `from` end, so activities can be ordered (`precedes`) and either end may sit on the other level (`task:<id>` or `domain:<id>` when an id is on both). `remove_relations: [{relation: [from, type, to], reason}]` takes one out;
- on a concept, next to its `support`, `support_kinds: {<ref>: <kind> | {kind, overlap, differs}}` says how a supporting concept relates when it is not simply a specialization (by default specialization, on the task level realization). It changes only a mapping this step added; one made at stage 4 or 6 is changed with `remap`. A concept taken out of `support` loses the mapping this step gave it. The decision, the supporting projects, the families and the reason are recorded per concept. Consolidation never reads the overlay target;
- `amend: [{ref, reason, alt, add_support, drop_support, support_kinds}]` changes only those fields of a shared concept an earlier decision made: its label, category, definition, status and note stay, and the reason is added to the recorded one. To change the label, category or definition, name the concept again;
- `remap: [{concept, target, kind, reason, overlap, differs}]` changes the kind of a project concept's mapping made at any stage, and `kind: null` removes it. A partial needs `overlap` and `differs` (§4); the mapping's note keeps the earlier note, marked `Earlier, as <kind>:`, and records the change and its reason. A mapping may not be removed while its concept is in the target's support (`amend` with `drop_support` first);
- a constraint, shared or in a project file, follows the concepts it names: one that constrains only removed concepts goes with them, a term naming a removed concept is unmarked, and a reference to a concept that moved level moves with it;
- `settled` and `disputed` say what a partial mapping or a contradiction does to a shared concept (§10). A `settled` entry whose mapping has since gone, or is no longer partial, settles nothing: `consolidate` and `update-check` warn about it, until a later file withdraws it with `unsettled: [{concept, target, reason}]`.

After each file, `support.projects` and `families` of every shared concept are refreshed: the projects of its evidence and of every project concept mapped onto it, as stage 7 counts them.

### 6. Verification

Every evidence claim (concepts, relations, constraints, variation choices, mappings) is checked: the cited lines are read from the source at the cited commit, and the decision port (or, in llm mode, a model other than the extractor) judges whether they support the claim. The **verified-evidence share** is reported. A claim whose evidence does not hold becomes `disputed` or is removed.

Low-confidence and disputed judgements (mapping kind, category), every `partial` mapping and **every evidence item the decision port judged unsupported** are rechecked by a model other than the one that made them. The default rechecker is `process.recheck` (another vendor, e.g. `{harness: codex, model: …}`); its `fallback` (e.g. the other Claude model) may answer only with a `fallback_reason`, and `verify --apply` refuses any other rechecker. The report counts rechecks by policy and gives the **recheck change rate** per item type, including `mapping-partial`, over the verdicts given in this run's pack; a verdict carried from an earlier pack is counted apart (`carried`), not again.

A mapping is rechecked against the target's shared definition as it is when `verify` runs: the owner's shared-level files after consolidation, or, for an overlay target, the release read in stage 4. The item carries it as `target` with `as_of`, and the pack says which version in `shared_definitions`. A verdict given against an earlier wording is not carried.

**What `verify --apply` removes.** A score from the decision port never removes a claim on its own: a cheap classifier is often reversed by the recheck. Evidence is dropped only when:
- its cited lines are missing at the cited commit (a mechanical fact);
- the recheck confirmed the negative judgement, or changed a judgement to `unsupported`;
- the owner decided so in `verify/<p>.owner.json`: `{by: <owner>, verdicts: [{claim, path, lines, verdict: supported | unsupported, reason}]}`, where an entry without `path` covers every evidence item of the claim. Only the owner writes this file, never an agent. Its verdicts override every other judgement.

A negative judgement that nothing confirmed keeps its evidence and its claim. Such a negative is left unanswered in the recheck, or has no independent rechecker. The evidence gets an `unverified` note, and so does the claim when all its evidence is of that kind. It is listed in `applied.unverified`, and the run report counts it. The recheck may leave these items unanswered; every other recheck item still needs a verdict and a reason.

Every removed claim is recorded in `applied.removed_claims` of `verify/<p>.json`, with its reason and its original form (`restore`). This includes a relation removed because one of its ends was removed, and a constraint removed with the concepts it constrains: they have `cascade_of` and their own evidence verdicts, since their evidence may have held. A constraint's term whose concept was removed is unmarked and listed in `applied.detached_terms`. A `broader` link to a removed concept is listed in `applied.detached_broader`. A mapping that the recheck changes to another target or kind obeys the rules of `map --apply`: a known target, a kind the preset allows, and for `partial` both `overlap` and `differs` in the verdict's value. They are written into the note, as in stage 4.

**Verifying again.** `verify --apply` runs once per verification. For claims added or changed afterwards (by a later decision, for example), `ontologizer verify <p> --changed` asks the decision port only about evidence whose claim wording or citation (path, lines, commit, kind) changed. It keeps the earlier judgements of the rest, and `--since <run>` keeps those of another run's verify. In both a full and a `--changed` verify, a recheck verdict given on the same content is carried into the new pack (`carried`) and is not asked again. The earlier answers file is set aside as `<p>.recheck.answers.prev.json`, because its ids are positions in the earlier pack. Then `verify --apply` runs as usual.

An **absence claim** ("the project has no X") requires a recorded protocol: what was searched for, where, and how (queries, paths, tools), and the result.

### 7. Stability and run report

Leave-one-out: for each project, the support of every shared concept is recomputed without that project. A concept that keeps support from at least two families under every removal is `stable`. One that drops below with some project removed is `fragile` and names that project. One without enough support even with all projects is `unsupported`, or `unconfirmed` when the frame admitted it below the confirmation threshold.

With constraints, the report adds per project their number by modality, the constraints' agreement, and the **terms without a concept**: what the statements mark `[[?term]]`, for the next extraction to add.

`ontologizer report` collects all metrics below (competency questions with a query run through the engine's `onto_cq_run`; consistency through `onto_reason` as a dry run) and the answer status of every competency question into `report.json` and `report.md`. For each design target it adds a fit matrix: requirements × projects, the concepts to reuse per requirement, and the projects ranked by fit (full = 1, partial = ½, averaged over the assessed requirements). `ontologizer report --compare A B` compares two runs mechanically; when both runs have stage 3 files, it also lists the matched pairs per project that are in both runs, only in A and only in B (what an ablation changed).

**Release comparison.** Two releases built by independent runs name the same concept differently, so ids do not say what was kept. `ontologizer release-compare A B` (from the owner of the shared levels, `--run` the run that built B) reads both releases from the store, proposes candidate pairs across them (TF-IDF top 3 each way, plus equal labels) and has the decision port judge them in batches of 20 pairs (stage `compare`, threshold `compare_same_concept`, by default the grouping one). The accepted pairs give each concept a kind: `kept` (same id), `renamed`, `split`, `merged`, `regrouped`, `dropped` or `added`. An added concept carries its consolidation reason and its review verdict. A dropped one is traced through run B's project concepts: `absorbed` (its counterparts map to B concepts), `rejected` (consolidation or review rejected their group), `one-family`, `project-only`, or `no-run-counterpart`. Both sides carry the nearest candidate below the threshold, release metrics (concepts per level, status, evidence per concept) and the run metrics of B, and of A with `--run-a`. The result is `runs/<B run>/release-compare-A-B.{md,json}`.

### 8. Review and calibration

`ontologizer review build` writes `review.yaml`. It lists every domain and task concept, with no cap, together with its support, families, stability, verification share and evidence, and every relation of the shared levels. The reviewing agent checks each one in the source and marks it `accept`, `reject` or `revise`. A shared level's constraints are listed too, under `constraints`, and judged as concepts are: a verdict stands while the statement, modality and constrained concepts are unchanged, and a rejected concept takes the constraints that constrain only it along, in the shared files and in the project files. Two things are flagged: `category_differs`, when a supporting project concept has another category (a recheck in stage 6 does not reach the shared concept), and `counterpart: false`, when no project relation stands behind a shared relation.

A project relation `x --t--> y` is a counterpart of the shared relation `A --t--> B` when it has the same type, goes in the same direction, `x` stands for `A` and `y` for `B`. A project concept stands for the shared concept it maps to (any mapping kind), and so does every project concept narrower than it (`broader`, any number of steps). It also stands for every shared concept that one specializes in turn: `broader`, or a `specialization` mapping between shared concepts, any number of steps. A relation between narrower concepts instantiates the relation between the wider ones, never the reverse: an edge of a wider project concept does not count for a narrower shared one. When there is no counterpart, `counterpart_other` lists the project relations between the same ends with another type or in the other direction.

Each supporting concept shows its stage 6 result in `verified`: the share of its evidence that held in this run or, when this run did not check it, in the latest earlier run (the `--since` run, then the run that review was built `--since`, and so on) that checked the evidence it has now, named in `verified_in`. Evidence changed since then is `unchecked`. A concept that stage 6 removed for want of evidence and that is back in the project file is `restored`, with the result that removed it in `verified_before`: check its evidence in the source.

A verdict stands only while what it judged is unchanged: the concept's label, category and definition, or the relation's ends and type. After an edit, `review build` reopens just what changed, so a correction needs no second full pass; a fresh pass tends to rewrite accepted wording rather than converge. `review build --since <run>` carries the accepted verdicts of an earlier run in the same way and marks them `carried_from: <run>`.

New support does not reopen a verdict, or every update would reset them all. A carried verdict whose supporting project concepts changed keeps `judged_support` (what it was given on) and `support_changed` (the concepts added and removed), and `review build` lists those items. The reviewer looks at each, changes the verdict if it no longer holds, and deletes `support_changed`; until then rebuilds keep the flag, and the gate records how many were still flagged.

A carried verdict is the earlier run's only while it is unchanged. To reopen one, set its `verdict` to null: rebuilds keep it open (`reopened: <run>`) instead of carrying it again. A verdict or comment the reviewer changes is the reviewer's own: it loses `carried_from`, its comment reaches the concept's note, and it does not count among the carried verdicts the gate records. `review apply` adds each review remark to a note once, so applying the same review again changes nothing.

Recording the gate is the owner's call: the reviewer's verdicts become the record only when the owner asks for `review apply` or runs it. `ontologizer review apply --by <model> --effort <effort>` makes the accepted ones `asserted` in the shared-level files and records the review gate with the reviewer. Only after that does the owner run `release`.

`ontologizer gold sample` draws a gold set: 80 items by default (`process.gold_size`), mixing evidence claims, categories, same-concept pairs (stages 3, 5 and `compare`) and triage classes. A strong agent that made none of the logged decisions labels it and records itself as `labelled_by: {model, effort, harness}`. `ontologizer gold calibrate` refuses a gold set without `labelled_by`, or labelled by a model that made any of its decisions. It compares the decisions with the labels and chooses each threshold for accuracy. `triage_keep` is chosen among the thresholds that lose at most `decisions.triage_max_loss` (default 0.03) of the architectural candidates. The thresholds and the measured accuracy are written to `calibration.yaml`, and the gold gate is recorded with the labeller. A kind with fewer than 20 items stays provisional and is reported, not applied, unless `process.apply_provisional` says so.

### 9. Overlay

The overlay target maps itself onto a released shared level: stages 2–4 and 6 run on the target alone, and nothing is consolidated from it. The result is the target's application layer and its verdict: what maps, what has no counterpart, and which shared concepts it lacks.

The run lives in the target owner's config:

- `process.overlay: true` and `process.frame` pointing at the study's frame (approved in the `gates.yaml` beside it). The stage commands then accept only the frame's overlay target, and `group`, `stability`, `review` and `gold` refuse to run.
- `shared:` and `validation_context` name the release graphs (`<level>@<ver>`): mappings point at the release's terms, and stage 4 reads the shared concepts from the store, since the target has no shared-level files. `map/<p>.json` then carries them for the answering agent.
- The extraction brief forbids the target's earlier analysis (its owner directory), so earlier results do not anchor the extraction.

`ontologizer overlay` writes `runs/<run>/overlay.{md,json}`: for each shared concept the target concepts that realize it and how, the concepts without a counterpart and the shared concepts the target lacks. With `--previous <earlier application file> --compare <release-compare JSON>` it also lists, per shared concept of the new release, whether the target realized it before (through the correspondence of the releases) and now: `still realized`, `newly realized`, `no longer realized`, `still lacking`, or `new concept, realized|lacking`, and the earlier mappings whose target was dropped.

### 10. Growing an ontology

An ontology of a topic does not wait for a corpus. It starts from what exists and improves with each source or correction, and nothing is confirmed until an independent source says so. Entering the shared levels and being confirmed are separate (§5).

**First version.** Create the owner with its name in the config (`project:`), and frame it with `admission_families: 1` and the source under `sources:`. Commit the source as text under git. For an RDF source, the `rdf` inventory adapter lists its named terms by kind (classes, SKOS concepts, properties, shapes, individuals, schemes, the header), so coverage is measured. Stages 1–8 run as usual: all the source's concepts are candidates in stage 5, and each stays a hypothesis.

**Each update is a new run.** A new source, or a correction written up as one (a file with its author and reason), goes through stages 1–4 against the current shared levels. Extraction still sees no shared vocabulary. The mapping then says what happens:

| Stage 4 | Effect |
| --- | --- |
| `equivalence` | the shared concept gains an origin; a second family confirms it, and the next review can make it `asserted` |
| `specialization` | a candidate for a narrower concept |
| none | a candidate for a new concept in stage 5 |
| `partial` or a contradiction | `disputed` at the review, with both readings kept, unless a decision settles it |

A new family means a new frame, so its approval is asked for again.

**Disputes need no question.** The rule is applied by the kit. A project concept that maps onto a shared one as `partial`, or that is itself `disputed`, is an open reading, and at `review apply` it makes the shared concept `disputed` whatever the verdict; the concept cites both sides and its note names them, so the earlier claim is never overwritten silently. The model deciding consolidation weighs each one. Where the shared definition holds for both sides, or the difference stays at project level, it records `settled: [{concept, target, reason}]` and the concept keeps its status. A contradiction no mapping shows goes in as `disputed: [{ref, reason}]`, and a later `settled: [{target, reason}]` ends it. A disputed statement keeps two readings, so a concept with one project concept behind it stays as it is, and `review apply` names it. A new source can reopen a concept only by bringing a new reading. `review build` shows the readings, and `update-check` and the report list what is disputed; none of this blocks a release.

**Writing the levels.** Levels that refer to each other, such as a task level with relations to new domain concepts and a domain level with phases on new activities, go in one call: `ontologizer ingest <domain> <task> <project>…`. Each file is checked against the others as they will be, and nothing is written unless all pass.

**The checks.** Before a release, `ontologizer update-check <base release> --base-run <the run that built it>` holds the working levels to the release. It needs no new source. It blocks on:
- a concept removed, or a relation gone, with no recorded reason;
- a constraint of the release gone with no recorded reason (`remove_constraints: [{ref: <level>:constraint/<id>, reason}]` in a consolidation file), unless every concept it constrained was removed with one;
- a changed concept that none of its judged cited passages supports, judged through the decision port at the cited commits;
- a competency question that lost its answer since the base run, unless the downgrade is recorded with a reason (below). Give questions a `query:`, so this does not rest on hand-written answers;
- a new clash, or a report older than the shared levels or the run's `cq.yaml`.

**A reasoned downgrade.** A hand-written status that was overstated in the base run should be lowered, not kept to pass the check. In the run's `cq.yaml` the question then carries `downgrade: {from: <the base run's status>, reason: <why it no longer holds>}`, e.g. `cq3: {status: partial, by: [...], downgrade: {from: answered, reason: "the earlier answer named a concept the sources do not define"}}`. `report` refuses a `downgrade` whose `from` is not above the new status or that has no reason. `update-check` lists the question under *Questions downgraded with a reason* instead of blocking, when `from` is the base run's status. A downgrade without a reason, or from another status, still blocks. A question answered by its `query:` takes its status from the store, so a `downgrade` does not apply to it.

**A retired question.** A question the new frame no longer has blocks, unless the frame lists it under `retired_questions: [{id, reason}]` (an id that is still a question is refused). The owner approves that frame, so the reason is the owner's word; `update-check` lists the question under *Questions retired by the frame*.

**The evidence share is reported, not a blocker.** For a changed or moved concept, the cited passages are the lines of every project concept mapped to it, under any mapping kind, and the decision port judges each against the new definition. The share of supporting passages mixes counterparts: the lines of a `specialization` or `partial` counterpart need not carry the whole shared definition, so a share below 1 is not a defect by itself, and a blocker on it would refuse a sound generalization. When no judged passage supports the change, nothing behind the concept says what it now says, and that blocks. Below `process.update_check_min_share` (default 0.5), the report gives a warning that names each passage that does not support the change (path, lines, project concept, mapping kind), to be read before the release. Passages that cannot be read at their commit, or that the decision port did not answer, are warnings too.

The result is `runs/<run>/update-check.{json,md}` and a recorded gate. With `process.update_check: true`, `release` of a level with an earlier release requires that gate, clean, over the files as they are; `--unreviewed` overrides it and is recorded. `release-compare <base> working` compares by meaning before the release, for concepts renamed or split.

Then `review build --since <base run>` and the review of what changed, as in §8. `review apply` writes into the shared-level files (statuses, review notes, rejected concepts and relations removed), so the update-check gate recorded before it no longer covers them, and `review apply` says so. The gate is not carried over such a change: a rejection removes concepts and relations, which is what the check must see. The order before a release is therefore:

1. `update-check` to find what blocks, and fix it;
2. `review build --since <base run>`, the review, `review apply`;
3. `report`, then `update-check` again, over the files as the review left them;
4. the owner's `release`.

**Foreign ontologies.** `import-graph` refuses a file with none of the kit's vocabulary in a graph with a role: the kit would see nothing there, and the write would replace the graph. To analyse such a file, make it a source as above. To keep it verbatim, import it into a graph with no role.

### Constraints

A constraint is what a project guarantees, forbids, permits or says it does not promise, stated in the terms of its concepts. It is a reified claim with its evidence, as a relation is, not an OWL axiom: an invariant is a norm the system may break, and the store holds the project's types, not its runtime state, so there is nothing to check it against. The normative text stays in the source; the ontology keeps the statement, its modality, what it constrains and where it is written, so it can be asked which constraints govern a concept and how projects differ in what they guarantee.

The classification follows SBVR. `modality` is one of `necessity` or `impossibility` (*structural*: it cannot be otherwise by definition), `obligation`, `prohibition` or `permission` (*operative*: it ought to hold and the system could break it), or `non-guarantee` (*boundary*: what the source says is not promised). The kind follows from the modality.

```json
{"id": "stale-result-rejected", "modality": "prohibition", "source_id": "INV-02", "level": "application",
 "statement": "a result of a stale [[assignment]] is accepted for its [[job]] after its [[?assignment epoch]] moved on",
 "constrains": ["assignment"], "evidence": [{"path": "docs/invariants.md", "lines": "12", "kind": "doc"}]}
```

The statement is the proposition the modality applies to, without the modal words. It is written only in the vocabulary's terms: `[[id]]` or `[[id|words]]` marks a concept (a local id, or `domain:<id>` / `task:<id>`), and every constrained concept is a marked term. The write route checks that every term and every constrained concept is a concept of the validation context, and that the kind follows from the modality. A term the statement needs that has no concept yet is either added as a concept (a value or property → `Quality` or `Mode`, a connection → `Relator`) or marked `[[?term]]`, and reported. Concepts are added when a rule needs them, not upfront; the relation types stay as they are.

Constraints of a shared level are reviewed in stage 8 and checked by `update-check`. Matching constraints across projects (stage 4 and 5) is not part of the process yet.

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
| Inventory coverage | inventory candidates counted for coverage (architectural ones when triage is on) that a reconciled concept cites ÷ all such candidates, over the kinds concepts cover (not properties, shapes, individuals, schemes or the ontology header); `unmeasured` without an inventory | 1, 3 |
| Inventory coverage by kind | per inventory kind: candidates listed in an `inventory` field (a concept's for classes and concepts; a concept's, relation's, caveat's or absence's for the other kinds), those whose line evidence only cites, and the missing ones by id | 1, 3 |
| Extraction agreement | matched concepts ÷ (concepts of A + concepts of B − matched), per project pair of extractors | 3 |
| Jev–LLM disagreement rate | questions answered by both Jev and an LLM (the category second opinion; the mapping candidate against the chosen target) where the answers differ ÷ such questions | 4, 6 |
| Verified-evidence share | evidence items judged supporting ÷ evidence items checked; unchecked items are counted separately | 6 |
| Verified-evidence share after the recheck | the same, with the recheck's verdicts (and the owner's) overriding the first judgement | 6 |
| Unverified negatives | evidence items judged unsupported that no recheck or owner confirmed, kept with their claims; with the number of claims removed in verification | 6 |
| Leave-one-out stability | shared concepts marked `stable` ÷ shared concepts | 7 |
| Competency-question coverage | questions answered ÷ questions; `partial` and `unanswered` are listed by id | 7 |
| Consistency clashes | contradictions the engine's reasoner finds over the store (`onto_reason`, RDFS, dry run); releases have their own IRIs, so a clash lies inside one version | 7 |
| Target fit (per target, per project) | mean over the target's assessed requirements of full = 1, partial = ½, none = 0 | 7 |
| Constraints' agreement | constraints two extractors state alike (modality, constrained concepts, cited lines) ÷ (constraints of A + of B − alike); only with constraints | 3, 7 |
| Terms without a concept | distinct `[[?term]]` marks in a project's constraints; only with constraints | 7 |

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
| 6 | `ontologizer verify PROJECT [--changed \| --since RUN]`, `ontologizer verify PROJECT --apply` |
| 7 | `ontologizer stability`, `ontologizer report`, `ontologizer report --compare A B`, `ontologizer release-compare A B [--run-a ID]` |
| 8 | `ontologizer review build`, `ontologizer review apply`, `ontologizer gold sample`, `ontologizer gold calibrate` |
| 9 | in the target owner's overlay run: `brief`, `reconcile`, `map`, `verify` on the target, then `ontologizer overlay [--previous FILE --compare FILE]` |
| 10 | `ontologizer update-check BASE --base-run RUN`, `ontologizer release-compare BASE working`, `ontologizer review build --since RUN` |

Each command accepts `--run ID` (default `process.run` in the config). The agent-side steps are in the skill [`analyse-corpus`](../skills/analyse-corpus/SKILL.md).
