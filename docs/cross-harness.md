# Cross-harness runs

An independent check is stronger when another harness and another model family make it. The kit does not launch harnesses and does not depend on any orchestrator. It writes a brief to a file and reads the result back as a file. Launching the session is the operator's step: the Codex CLI directly, or any session launcher the operator uses.

## Where the process uses it

| Stage | What runs elsewhere | Brief | Result, read back by the kit |
| --- | --- | --- | --- |
| 2 Double extraction | an additional independent extraction (extractor with `harness: codex`) | `runs/<run>/briefs/extract-<p>-<id>.md` from `ontologizer brief extract <p> --extractor <id>` | `runs/<run>/extract/<p>/<id>.json` |
| 6 Verification | rechecks of disputed and low-confidence judgements | `runs/<run>/verify/<p>.recheck.json` (its `instructions` are the brief) | `runs/<run>/verify/<p>.recheck.answers.json` |
| any decision stage in `llm` mode | answers to narrow questions | `runs/<run>/decisions/<stage>.pending.json` | `runs/<run>/decisions/<stage>.answers.json` |

## Configuring an extractor in another harness

```yaml
process:
  extractors:
    - {id: opus, harness: claude, model: claude-opus-5-5}
    - {id: sonnet, harness: claude, model: claude-sonnet-5-5}
    - {id: codex, harness: codex, model: <OpenAI model id>, optional: true}
```

`ontologizer brief extract <p> --extractor codex` writes the brief. Two ways to launch a session on it:

**Codex CLI directly** (used in the pilot):

```sh
codex exec -m <OpenAI model id> -s workspace-write -C <owner dir> --skip-git-repo-check \
    -o runs/<run>/extract/<p>/codex.last.md \
    "Read the brief at runs/<run>/briefs/extract-<p>-codex.md and do exactly what it says. Your run_id is <id>."
```

`workspace-write` lets the session write inside the owner repository and read the source checkout. The validate command in the brief needs the store; if the sandbox cannot reach it, the coordinator validates the file afterwards.

**Through a session launcher.** Any orchestrator that can start a Codex (or other non-Claude) session on a file works. If it insists on its own brief format, wrap the kit's brief in it, with the kit's brief as the task and its output file as the only file the session may write. The kit does not generate such wrappers: it would tie the kit to one launcher.

Either way:
- the session works in the owner repository and writes only the output file named in the brief;
- the kit does not wait for it: re-run stage 3 once the file exists.

## A rechecking session

Point the session at `verify/<p>.recheck.json`. Its `instructions` field is the brief, and each item names the model that made the judgement (`author`). The recheck must be made by a different model: `verify --apply` refuses answers whose `model` made any of the judgements.

## What the result must record

Every cross-harness result names its harness, its model and its run id, so metrics can separate it from the Claude runs:
- extraction: `"extractor": {"id", "harness": "codex", "model", "run_id": "<session or stream id>"}`;
- recheck: `{"model", "harness": "codex", "run_id"}` at the top of the answers file;
- llm-mode decisions: `"model"` at the top of the answers file.

The run report lists every extraction with its harness, model and run id, and counts rechecks by harness.

## Without a second harness

When no non-Claude harness is available, the same stages run with Claude models only:
- stage 2 uses the two Claude extractors; an extractor marked `optional: true` with no output is skipped;
- stage 6 rechecks go to the other Claude model.

The run report then says `Claude models only: no cross-harness results in this run`.
