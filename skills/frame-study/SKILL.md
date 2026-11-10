---
name: frame-study
description: Use when a new comparative ontology study (any topic) is about to start, or its scope changes — stage 0 of the analysis process. A research agent writes frame.md (domains and boundaries, ontologies to reuse, the corpus by architectural family, competency questions, levels preset, overlay target, out of scope); the owner approves it as a recorded gate before stage 1.
---

# frame-study (stage 0)

The frame fixes what the study is about before anything is extracted. Every later stage reads it: the inventory and the briefs take the corpus and the domains from it, consolidation counts support by its families, the run report answers its competency questions, and every stage refuses to read its overlay target. The process is in the kit's `docs/process.md`.

## Steps

1. **Start the file.** From the owner repository: `uv run --project <kit> ontologizer frame init`. This copies `templates/frame.md` to `frame.md` (or to `process.frame` in the config).
2. **Dispatch one research agent** (strong model: Opus) with the owner's question, the candidate corpus and this skill. It writes `frame.md`:
   - **Domains.** One or more, each with a boundary: what is inside, and which neighbouring domain is kept outside.
   - **Reuse.** Existing ontologies and standards of the domain. Search for them; do not assume there are none. For each, say what is taken and why, and list what was considered and rejected. The upper ontology comes from the preset (gUFO for `guarino-4`).
   - **Corpus by architectural family.** Group the projects by architecture, not by product category. Two projects belong to one family when they would support a shared concept for the same architectural reason: the same runtime model, the same state store, the same coordination mechanism. Write the reason for each grouping. At least two families, unless the owner wants the ontology to grow from whatever sources exist: then set `admission_families: 1` (`docs/process.md` §5). Each project is in exactly one family. A text about a subject is marked `sources: {<name>: {kind: description, origin: …}}`, and a retelling adds `derived_from: <source>` and sits in that source's family. The frame's `study` repeats the config's `project`, a name chosen for the topic and not taken from a source.
   - **Competency questions.** 5–15 questions that the finished ontology must answer. Tag each with its level. When a question can be answered by a SPARQL query over the store, add the query file under `queries/` and name it in `query:`. When a frame of an ontology that already has a release drops a question, list it under `retired_questions: [{id, reason}]`; otherwise the update check blocks on the lost question (docs/process.md §10).
   - **Levels.** The preset and any deviation from it.
   - **Overlay.** The project or projects that will later be mapped onto a release and must not be read while the shared levels are built. Usually this is the owner's own project.
   - **Out of scope.**
   The research agent reads documentation and code structure only as far as grouping needs. It does not extract concepts. Its output is the frame, not an analysis.
3. **Check:** `ontologizer frame check`. Fix every error the check reports.
4. **Owner review.** Show the owner the frame. Retell its families and competency questions in the owner's language. Changes go into the file.
5. **Gate:** after the owner's explicit approval in the live channel, `ontologizer gate approve frame --by <owner>`. This records the frame's sha256 in `gates.yaml`. Any later edit of the frame invalidates the approval, and stage 1 refuses to start until it is approved again.

## Rules

- Never approve the frame on the owner's behalf. Approval is the owner's word, not a passing check.
- The frame contains no shared concepts. Seed vocabularies anchored the extractions of earlier runs, and the process removed them.
- Keep the frame topic-neutral in form: the template's sections, the topic in the content.
