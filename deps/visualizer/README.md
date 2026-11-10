# Visualizer container

A read-only web view of the ontology ([`docs/visualizer.md`](../../docs/visualizer.md)). It runs as
its own container beside the engine, in the engine's compose project, so that it reaches the engine
as `engine` and no visualizer command can recreate or remove the engine's container or its volume.

```sh
make viz-up       # build the image and start it at http://127.0.0.1:8090/
make viz-status   # container, address, whether it answers
make viz-logs
make viz-down     # remove the visualizer's container; the engine keeps running
```

`make oo-down` stops the visualizer first, because the two share a network.

## Where to run what

| | Host, main checkout | Host, git worktree | VM with its own Docker daemon |
| --- | --- | --- | --- |
| `make viz-up` | yes: the normal case | yes, same result; the image is built from the worktree's code | yes, for testing: the container runs on the VM's daemon |
| Engine for the container | `http://engine:8080` | `http://engine:8080` | `http://host.docker.internal:8080` (the host's engine, read only) |
| Browser | `http://127.0.0.1:8090/` on the host | the same | only inside the VM; the host browser does not see it |
| Generated files | `var/visualizer/` of the main checkout | the same | the same (the main checkout is reached through the worktree) |
| Saved views | each owner's `paths.views` (default `views/`) | the same | the same directories, written as the VM's user |

The engine address is chosen by `viz.sh`: `engine` when the engine's container runs on this Docker
daemon, else `host.docker.internal` with the port of the hub's `store.url`. `VIZ_STORE_URL`
overrides it and `VIZ_PORT` changes the port.

## What `make viz-up` does

1. **Reads the owner registry** of the hub (`ontologizer.local.yaml`; a worktree uses the main
   checkout's) and writes two files to `var/visualizer/` of the main checkout:
   - `ontologizer.local.yaml`: the registry as the container sees it (`/owners/<name>`) and the store address;
   - `compose.mounts.yaml`: each owner's directory read-only and its views directory read-write.

   An owner that does not exist on this machine is skipped with a note. So is one whose views
   directory lies outside its own directory. A missing views directory is created, empty, so that it
   belongs to you.
2. **Builds the image** from `deps/visualizer/Dockerfile`. Only the files allowed by
   `Dockerfile.dockerignore` are sent: the kit's source, kit vocabulary, presets, the pinned upper
   ontology, schemas and the lock file. `.env`, `var/` and the engine clone stay out. Base images are
   pinned by digest; dependencies come from `uv.lock`.
3. **Starts the container** with `--no-deps`, so the engine is not touched, and waits for its health
   check.

## The container

- **Token.** It is passed in the environment from `scripts/oo`. It is in no image layer and in no
  generated file.
- **User.** The process runs as your UID and GID, so a saved view belongs to you.
- **Hardening.** Read-only root filesystem, no Linux capabilities, `no-new-privileges`, a tmpfs at
  `/tmp`. No Docker socket.
- **Ports.** It publishes on `127.0.0.1` only.
- **Host check.** It answers only to the `Host` it is published under (`127.0.0.1:PORT`,
  `localhost:PORT`).
