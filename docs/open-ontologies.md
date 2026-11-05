# Open Ontologies: what the kit relies on

This page covers the engine version, server setup, the behaviour verified on the engine, the rules the kit follows because of it, and backup. Everything was verified on **Open Ontologies v2.1.0** (122 MCP tools, profile `full`) on 2026-11-04, using the engine's source, documentation and `scripts/engine_check.py`. Re-run the check when the engine is upgraded; see [Re-checking](#re-checking).

## Server setup

The kit expects one shared server, at `store.url` in `ontologizer.yaml`, by default `http://127.0.0.1:8080`. Requests use REST at `/api/...` and MCP at `/mcp`, authenticated with `Authorization: Bearer $OPEN_ONTOLOGIES_TOKEN` (see `scripts/oo`).

Settings that matter (`config.toml`):

```toml
[storage]
mode = "persistent"   # the default graph lives in memory only

[cache]
auto_refresh = false  # a changed source file must never replace the accepted graph
idle_ttl_secs = 0
```

- **Data directory.** Set it with `--data-dir`; the default is `~/.open-ontologies`. It holds:
  - `triplestore/`: the RDF store (Oxigraph on RocksDB);
  - `open-ontologies.db`: SQLite with history, named versions and service state;
  - `config.toml`.
- **Docker.** Mount the data directory as a persistent volume and pin the image version. `make oo-server` does this for a local server; see [`deps/open-ontologies/README.md`](../deps/open-ontologies/README.md).
- **One writer.** A RocksDB directory accepts only one writing process. All agents and the kit must go through the same server, never through processes of their own over that directory.
- **No LLM needed.** The engine requires no LLM or API key. Agents connect as MCP clients with their own models.
- **Sizing.** 1–2 vCPU and 4–8 GB RAM is a starting point for a small store. Measure memory after load and the time of typical queries, writes and exports before relying on it.

## Verified behaviour

| Operation | What it does | What the kit does |
| --- | --- | --- |
| `INSERT DATA { GRAPH <g> { … } }` via `POST /api/update` | Writes to `g` only | Main write path |
| `DROP SILENT GRAPH <g> ; INSERT DATA { GRAPH <g> … }` in one request | Replaces one graph; other graphs are untouched; one request is one Oxigraph transaction | `replace_graph` |
| `DELETE WHERE { GRAPH <g> … }` | Clears `g` only | — |
| `CONSTRUCT … WHERE { GRAPH <g> … }` | Returns terms in N-Triples syntax; export and re-import are lossless; a Turtle round trip through rdflib is isomorphic | `export_graph` |
| Update without `GRAPH` | Writes to the default graph | Rejected by the write route |
| One update naming several graphs | Runs | Rejected by the write route |
| `onto_query` | Runs SPARQL queries, not updates | Reads use `/api/query`, updates use `/api/update` |
| `onto_shacl` | Validates the union of all graphs (`selector: whole-store`); a single graph cannot be selected | Validates the candidate locally (pyshacl); `onto_shacl` is only for whole-store audits |
| `onto_load(path)` | **Replaces the whole dataset.** A Turtle file collapses all named graphs into the default graph | Never called |
| `onto_load(turtle)` (inline) | Adds to the default graph | Not used: no graph can be named |
| `onto_plan(new_turtle)` | Treats the input as the complete desired state and diffs it against the whole store: a class from a named graph shows as removed | Never called; its report is wrong for named graphs |
| `onto_apply` | Replaces only the default graph; named graphs stay | Never called; its effect differs from the plan it reports |
| `onto_version` / `onto_rollback` | Snapshot and rollback of the whole dataset, stored as full N-Quads | Never called on the shared store; per-graph versions are release graphs `…@ver` |

Other findings:

- **The server sees its own filesystem only.** In a container (home `/data`), repository paths do not exist for it, so `onto_load(path)` and `onto_save(path)` work only on files inside the container. The kit moves data only through REST and SPARQL.
- **REST errors can be lost.** `/api/update` and `/api/query` embed the error text in JSON without escaping. An error containing a quote yields invalid JSON, and the response body is `null`. The adapter treats a `null` body as an engine error.
- **Version labels stay.** The `--dataset-ops` check leaves the versions `probe-v1` and `ontologizer-probe` in `onto_history`, and MCP cannot delete them. They are SQLite records and hold no graph data.
- **Restart durability was not checked**, because the server is managed outside the kit.

## Engine tools against kit functions (ONTO-18)

The kit should use what the engine already does instead of a second implementation beside it. Each candidate was measured on a pilot corpus (four projects, one per architectural family, ~400 concepts, reference = the reviewed mappings and shared levels of the run), with the engine called over MCP with `dry_run`/`materialize: false` and inline Turtle, so nothing was written to the shared store.

| Kit function | Engine tool | Result | Decision |
| --- | --- | --- | --- |
| Stage 4: candidate shared target per project concept (Jev `choice` over the shared concepts) | `onto_align` (many-to-one, no embeddings) | hit@1 **0.19** vs Jev **0.86**; recall@3 0.19 vs 0.91; no candidate at all for 64 of 232 mapped concepts. Names differ across projects while the meaning is shared; label and structure signals do not reach it. | keep the kit's |
| Stage 5: candidate same-concept pairs across projects (TF-IDF, then Jev `noul`) | `onto_align` per project pair | As a candidate source F1 0.28–0.30 vs TF-IDF 0.32, but complementary: the union lifts candidate recall 0.245 → 0.349. After Jev judges the extra 299 pairs, accepted-pair recall moves only 0.104 → 0.113 (+1 pair, $0.004): the limit is the judgement, not the candidates. | keep the kit's |
| `onto_align` embedding signal | `onto_embed` | not measured: the server has no embedding model loaded (`open-ontologies init` not run in the container) | closed without measuring (owner's decision): the kit's path is kept |
| Competency questions by SPARQL (report) | `onto_cq_run` | same answers (cq1 516 rows, cq3 passed), plus query pitfalls (e.g. returned IRIs without `rdfs:label`) | **adopted**: the report calls it over the adapter's MCP path; the kit's SELECT check is the fallback |
| Reasoning into `inferred/*` (not implemented) | `onto_reason` | works (RDFS, 0.4 s dry run, 16.5k inferences, certifiable clashes) and found 12 disjointness clashes — all from **mixing versions**: release graphs reuse the working graphs' term IRIs, and the reasoner reads the whole store (no graph selector). Inside one version the categories are consistent. | **adopted** after ONTO-19 gave releases version-distinct IRIs: the run report runs it as a dry run and reports the clashes (0 on the pilot store after migration, 30.5k inferences) |
| SHACL per candidate graph (pyshacl) | `onto_shacl` | validates the whole store only | keep the kit's |
| JSON → RDF converter + per-graph write route | `onto_ingest` / `onto_map` | reads a file on the server's filesystem (not the owner's repository), no named-graph target | keep the kit's |
| Extraction schema | `onto_extract_scaffold` / `onto_extract_validate` | instance extraction for one class; the kit extracts the concepts themselves | not applicable |

Rule kept from this: engine tools that act on the whole dataset or auto-apply (`onto_align` without `dry_run`, `onto_reason` with `materialize`) are never called on the shared store.

## Rules the kit follows

1. The engine port has per-graph operations only:
   - `add_to_graph`, `remove_from_graph`, `replace_graph`, `export_graph`, `drop_graph`;
   - SELECT, ASK and CONSTRUCT queries.
2. The adapter does not call `onto_load(path)`, `onto_plan`, `onto_apply`, `onto_clear` or `onto_rollback`. Nothing stops an agent from calling them directly over MCP. The rule in `AGENTS.md` is a convention for trusted sessions, not a mechanical guarantee.
3. Each candidate graph is validated locally with pyshacl, together with the graphs it depends on (`validation_context`), before it is written.
4. A version of one graph is a release graph `PATH@VER`. `onto_version` is useful only as a backup of the whole store.

## Backup and restore

The markdown exports are a view, not a backup: they leave out axioms, constraint forms and some metadata. There are two levels:

| Level | Holds | Restores |
| --- | --- | --- |
| Portable RDF snapshot: the whole dataset as TriG or N-Quads, plus version metadata | All graphs, named graphs included | The structured data |
| Application backup: RocksDB, SQLite and `config.toml` | Graphs, history, named versions, service state | The running server |

RocksDB and SQLite are separate stores, so copy them consistently: stop writes or the service while copying. Copying live database files without such a procedure is not a tested backup. Verify a restore on a separate instance: key concepts present, typical queries correct, an export reproducible.

To move or share a single graph, use `ontologizer export-graph PATH --turtle` and `import-graph PATH FILE.ttl`.

## Re-checking

```sh
uv run scripts/engine_check.py                 # per-graph checks; safe on a populated store
uv run scripts/engine_check.py --dataset-ops   # also whole-dataset operations; runs only on a probe-only store
```

The whole-dataset checks run only when the store holds nothing but `urn:ontologizer:graph:probe/*` graphs. On any other store they are skipped. They create the version labels listed above.
