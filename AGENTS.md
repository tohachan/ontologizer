# Ontologizer

## Open Ontologies

Engine: `http://127.0.0.1:8080` (MCP at `/mcp`, REST at `/api/...`). The same URL works on the host and inside a VM or container that forwards the port.

Auth: `Authorization: Bearer $OPEN_ONTOLOGIES_TOKEN`. Get the token through `scripts/oo`, never hardcode it or print it:

- `scripts/oo curl api/stats`: authenticated request
- `eval "$(scripts/oo export)"`: export `OPEN_ONTOLOGIES_TOKEN` and `OPEN_ONTOLOGIES_URL`

The token is read from `.env` at the repo root. In a git worktree without its own `.env`, it falls back to the main checkout's `.env`, then to the environment variable. `.env` is gitignored; `.env.example` shows the format.

Local server: `make oo-status` shows whether the engine clone exists and the server answers; `make oo-server` starts it only when nothing answers (clone, Docker build, token in `.env`). Its config is `deps/open-ontologies/defaults.conf`, overridden per machine by the gitignored `local.conf`. Never run `docker compose down -v`: it deletes the store's volume.

REST gotcha: `/api/query` and `/api/update` take SPARQL in the `query` field.

**In a worktree or a VM.** Nothing needs setting up: `scripts/oo` and `uv run ontologizer …` work as they do in the main checkout, and `.env` and relative config paths fall back to it.

- One server, one store. A worktree has no store of its own, so its writes reach everyone.
- The `server:` line of `make oo-status` decides whether the engine is available, not `docker ps`. A session may run in a VM with its own Docker daemon, while the engine's container lives on the host and only its port is forwarded. `container: none on this Docker daemon` is then the normal state.
- With the server elsewhere, start or rebuild nothing here. `make oo-rebuild` and `make oo-down` refuse. `make oo-server` does nothing while the server answers. If the server does not answer, report that to the owner instead of running it: it would create a second engine with an empty store.
- A container started on that VM's daemon reaches the server at `http://host.docker.internal:8080`. `127.0.0.1` inside a container is the container itself, and the host-gateway address is the VM, not the host.
- Inside a container set the store address through `store.url` in `ontologizer.local.yaml`: it beats `OPEN_ONTOLOGIES_URL`.

**Review gate.** It is the `codex-review` command of the `codex-gates` plugin. A session that sees only the `codex-gated-development` skill does not have it: a command is not a skill, and a directory of skill links does not install a plugin. Check with the `agents-developer` preflight; if it reports `STOP`, install the plugin with `claude plugin marketplace add <source>` and `claude plugin install codex-gates@codex-gates-local`. The gate depends on the session's environment, not on the worktree.

## ontologizer kit

This repository is the tool only. It must not reference any particular study or project: not in code, config, tests or docs. The one external dependency is Open Ontologies. The repository holds:
- the Python package `src/ontologizer`, run with `uv run`;
- the level presets in `presets/`: data from which SHACL is generated;
- the kit vocabulary in `kit/` and the pinned upper ontology in `vendor/`;
- skills and tests.

Studies and project ontologies live in their own repositories.

- `ontologizer status | bootstrap | ingest FILE.json | validate FILE.json | export-graph PATH | import-graph PATH FILE | release PATH… VER | export-md | query FILE.rq | consolidation-report | consolidate FILE.yaml`
- **Analysis process** (`docs/process.md`): stage 0 `frame init|check`, `gate approve frame --by NAME`; stages 1–8 `inventory`, `brief extract`, `reconcile`, `map`, `group`, `verify`, `stability`, `report [--compare]`, `review build|apply`, `gold sample|calibrate`, all with `--run ID`; stage 10 (an update of an existing ontology) `update-check BASE --base-run RUN`, `review build --since RUN`. Skills `frame-study` and `analyse-corpus`. A strong agent gives the review's verdicts; approving the frame, recording the review gate and releasing are the owner's word, never the agent's own decision.
- **Adapters.** Evidence, inventory and decisions adapters are chosen by name in the owner's config and resolved through entry points (`core/plugins.py`). Built in: `checkout` (evidence), `graphify` and `rdf` (inventory), `jev` (decisions; key `OPENROUTER_API_KEY` in `.env`, never print it). Other adapters come from any package declaring the entry points, added with `uv run --with <package>` (README §Adapters).
- **Config cascade** (`src/ontologizer/core/config.py`):
  - `ontologizer.yaml` here is the **hub**: the store, the namespace, the preset and the kit's own graphs (`upper/gufo`, `kit/core`, `inferred/*`).
  - The owners of other graphs are registered per machine in `ontologizer.local.yaml`, which is gitignored; `ontologizer.local.yaml.example` shows the format.
  - An owner's `ontologizer.yaml` has `hub:` and declares:
    - its graphs;
    - `shared:`, the extraction prefixes mapped to the shared-level graphs;
    - `validation_context`, `paths:`, `exporters` and `evidence`.
  - Literal patterns win over globs. A config writes and releases only the graphs it owns. Its export has pages only for its own graphs; other graphs reach its views through `context:`.
- **Layout by level.** An owner's ontology (extraction JSON) sits under `ontology/`, one directory per Guarino level: `ontology/domain/`, `ontology/task/`, `ontology/application/`, plus `ontology/variation/` for the second axis; its export mirrors the levels under `exports/`. The export adds `top/<upper>.md` and `compare/`. The level of a graph comes from its `role` through the preset. Details are in README §Owner repository.
- **Running.** From an owner's directory: `uv run --project <path to this repo> ontologizer …`. Without `--config` the CLI uses `$ONTOLOGIZER_CONFIG`, else the nearest `onto/ontologizer.yaml` or `ontologizer.yaml` at or above the working directory. `$ONTOLOGIZER_HUB` overrides `hub:`. In a git worktree, relative paths are also tried from the main checkout.
- **Writes.** Write only through the kit's write route (`ingest`, `import-graph`, `release`). Never call `onto_load(path)`, `onto_plan`, `onto_apply`, `onto_clear` or `onto_rollback` on the shared store: they replace every graph (`docs/open-ontologies.md`).
- **Agents.** They produce extraction JSON, never RDF. The workflow is `skills/analyse-corpus/SKILL.md` (a single repository or an overlay: `skills/map-repo/SKILL.md`). Questions to an existing ontology (exploring it, checking a concept against it, answering only in its terms) follow `skills/query-ontology/SKILL.md`, read-only.
- **Generated, gitignored:** `var/ops.jsonl` (the operations log of the store) and `shapes/` (reference copies of the generated SHACL).
- **Tests and checks:** `uv run pytest`. They are hermetic: a temporary hub and study, no store. Engine check: `uv run scripts/engine_check.py` (add `--dataset-ops` only on an empty store).
