# Local Open Ontologies server

The kit needs one Open Ontologies server (`store.url` in `ontologizer.yaml`). These files build the engine's HTTP/MCP service from a local clone of the engine and run it in Docker. The engine repository itself is not changed. Nothing is copied into it.

```sh
make oo-status    # config, clone, server and token check, container
make oo-server    # start the server unless one already answers at OO_URL
```

## What `make oo-server` does

1. **Checks the running server first.** If `OO_URL/health` answers and `/api/stats` accepts the token from `.env`, it stops there. If a server answers but rejects the token, it stops with an error and does not touch that server.
2. **Clones the engine** from `OO_REPO` into `OO_SRC` at `OO_REF`, but only if no clone exists yet. An existing clone is used as it is: it is never pulled or switched to another ref.
3. **Writes a token** if `.env` has none. The token is 32 random bytes in hex, written to `OPEN_ONTOLOGIES_TOKEN` in the `.env` that `scripts/oo` reads, with mode `0600`.
4. **Builds and starts the container** with `docker compose build` and `up -d --wait`. Then it checks that the token is accepted.

`make oo-rebuild` builds the image again and recreates the container, for example after you update the clone. `make oo-down` stops the server. Neither one deletes the data volume.

## Configuration

| File | Committed | Holds |
| --- | --- | --- |
| `defaults.conf` | yes | `OO_REPO`, `OO_REF` (ref of fresh clones, `v2.1.0`), `OO_SRC` (`oo`), `OO_URL` |
| `local.conf` | no (gitignored) | per-machine overrides; see `local.conf.example` |

Precedence, highest first:
1. the environment or the make command line, e.g. `make oo-server OO_SRC=../open-ontologies`;
2. `local.conf`;
3. `defaults.conf`.

A relative `OO_SRC` is resolved from the root of the main checkout. The default `oo/` is gitignored. In a git worktree without its own `local.conf`, the main checkout's `local.conf` is used, so all worktrees share one clone and one server.

`OO_URL` must equal `store.url`. The host port that compose publishes on `127.0.0.1` is taken from it. If `OO_URL` is not on this machine, `oo-server` refuses to start anything.

Set `CARGO_FEATURES` and `CARGO_BUILD_JOBS` in the environment to change the build; they are described below.

## The container

`compose.yaml` fixes the compose project name to `open-ontologies`. The container (`open-ontologies-engine-1`) and the data volume (`open-ontologies_ontology-data`) therefore stay the same, whichever directory started them. There is one server per machine.

- **Build.** The build context is the clone. `Dockerfile.service` and `Dockerfile.service.dockerignore` live here, and the ignore file next to the Dockerfile applies even though the context is elsewhere. Only `Cargo.toml`, `Cargo.lock`, `.cargo/` and `src/**/*.rs` are sent. Datasets, prebuilt binaries, Git history and local secrets stay out.
- **Pinning.** The Rust and Debian base images are pinned by digest, and Cargo runs with `--locked`. Debian packages still come from the repositories at build time, so the build is not bit-for-bit reproducible.
- **Features.** The default is `CARGO_FEATURES=embeddings,plugins`, the same as the engine's release workflow. Add `,sql` for PostgreSQL and DuckDB. An empty value builds the core engine. `CARGO_BUILD_JOBS` defaults to 2 to limit RAM on Docker Desktop.
- **First build.** It compiles the engine and needs internet access. It takes a long time and several GB of Docker disk; the cargo caches speed up later builds.
- **Hardening.** The process runs as UID/GID 10001 with a read-only root filesystem, no Linux capabilities, `no-new-privileges` and a tmpfs at `/tmp`. No host directories or Docker socket are mounted.
- **Data.** `/data` is the named volume. It keeps the store and SQLite state through `restart` and `down`. Only `docker compose down -v` deletes it; no make target does that. An optional `/data/config.toml` supplies engine settings (see [`docs/open-ontologies.md`](../../docs/open-ontologies.md#server-setup)).
- **Ports.** `compose.yaml` alone publishes no port. `compose.localhost.yaml` publishes `127.0.0.1:${OPEN_ONTOLOGIES_HOST_PORT}`, and `server.sh` always adds it.
- **Auth.** `/health` is public. `/api/*` and `/mcp` require `Authorization: Bearer <token>`. The token is passed to the container from `.env` through `scripts/oo`, never from the shell's own variable when `.env` has one.
- **Not included.** Embedding model weights, external proof checkers and solvers, and the optional Python causal backend are not in the image.

`make oo-logs` shows the last lines of the engine log. The health check proves HTTP liveness only, not that every optional feature is configured.
