# Visualizer agent: contract

The left sidebar of the visualizer is a chat with an agent that rebuilds the open view on request. The
agent is a real harness, Claude Code or Codex, run as a CLI by the visualizer service. Its only tools are
the view tools below. This file fixes the contracts between the three parts, so that they can be built
apart:

- **View tools** (ONTO-44): an MCP server inside the service.
- **Harness bridge** (ONTO-45): sessions, the harness processes, sign-in, and the stream to the page.
- **Chat UI** (ONTO-46): the sidebar tabs and the chat.

Decisions of the owner (2026-11-10):

- A chat through the server, not a terminal emulator. If sign-in from the page cannot work, the fallback is
  an xterm and no chat server.
- Both harnesses, switchable from the page. Claude runs through its CLI (no SDK), Codex through `codex exec`.
- Sign-in is by subscription, from the page, kept in a volume of the container. Claude signs in with
  `claude auth login --claudeai`, never `claude setup-token`. Codex signs in with `codex login --device-auth`.
- One session per browser tab, not kept across reloads.

Requests are point operations on the current view ("from what is shown now, make what I ask"), so a
session's history only has to last as long as the tab.

## 1. Safety

- The agent never writes the store and runs no SPARQL. It has no shell, no file access and no tools other
  than the view tools. The service starts each harness so: Claude with `--strict-mcp-config` and an
  allow-list of the view tools only, every built-in tool disallowed; Codex with a read-only sandbox, in an
  empty temporary working directory, with the view MCP server only. ONTO-45 documents what each harness
  can still do, and how that is limited.
- The service never sends credentials to the page, never logs them, and never puts them in a command line.
- A session is bound to one tab by a random id, plus a secret the MCP endpoint requires
  (`Authorization: Bearer <secret>`). The secret goes only to the harness process, through its MCP config.
- The service never starts a harness unless the hub enables the agent. The setting is
  `visualizer.agent.enabled: true` in the hub's config, usually `ontologizer.local.yaml`; it is off by
  default. With the agent off, every `/agent` route answers HTTP 501 with the code `agent-disabled`.

## 2. HTTP API

All routes are under `/visualizer/api`. They keep the service's host and origin guard and its JSON error
shape (`{"error": {"code", "message", "details"}}`).

| Route | Body | Answer |
| --- | --- | --- |
| `GET agent/harnesses` | — | `{"harnesses": [{"name": "claude" \| "codex", "installed": bool, "signedIn": bool, "version": str \| null}]}` |
| `POST agent/harnesses/{name}/login` | `{}` | `{"loginId"}`. Starts the sign-in (§5). |
| `GET agent/logins/{loginId}` | — | `{"state": "starting" \| "open-url" \| "enter-code" \| "show-code" \| "done" \| "failed", "url": str \| null, "userCode": str \| null, "message": str \| null}` |
| `POST agent/logins/{loginId}/code` | `{"code": str}` | `{"state"}`. For a flow that asks for a code to paste back. |
| `DELETE agent/logins/{loginId}` | — | `{}`. Cancels the sign-in. |
| `POST agent/sessions` | `{"owner": str, "harness": "claude" \| "codex"}` | `{"sessionId"}` |
| `DELETE agent/sessions/{sessionId}` | — | `{}`. Ends the session and kills its process. |
| `POST agent/sessions/{sessionId}/chat` | the AI SDK chat request (§3) | An SSE stream (§3) |
| `POST agent/sessions/{sessionId}/stop` | `{}` | `{}`. Kills the turn in progress; the stream ends with an `abort` chunk. |

The harness used for a session is fixed when the session is created. Switching harness on the page ends
the session and starts a new one.

## 3. The chat stream: AI SDK UI message stream

The page uses the AI SDK chat protocol: `ai@7.0.153` with `@ai-sdk/react@4.0.157`, optionally with
assistant-ui (`@assistant-ui/react@0.15.31`, `@assistant-ui/react-ai-sdk@1.4.20`) for the components.
Those are the pinned versions. The exact chunk names and fields are those of `uiMessageChunkSchema` in
`ai@7.0.153`. Both sides read them from that package, not from memory.

**Request.** `POST agent/sessions/{id}/chat`. The body is what the AI SDK's `DefaultChatTransport` sends:
`{"id", "messages": [UIMessage…], "trigger", "messageId"}`. The page adds `"view"`:

```json
{"view": {"spec": "<the ViewSpec of the open view>", "compileId": "...", "picked": ["<node or edge id>"]}}
```

The service takes the text of the last user message as the turn's input. The harness keeps the
conversation itself (`--resume` for Claude, `exec resume` for Codex). Before the turn starts, the service
stores `view` as the session's current view; the tools read it.

**Response.**

- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `x-vercel-ai-ui-message-stream: v1`.
- Each chunk is `data: <json>\n\n`, and the stream ends with `data: [DONE]\n\n`.
- The service turns each harness's event stream into these chunks:

| Harness event | Chunks |
| --- | --- |
| start of turn | `start` (with `messageId`), `start-step` |
| assistant text | `text-start`, `text-delta`… , `text-end` (one id per text block) |
| thinking or reasoning, when the harness gives it | `reasoning-start`, `reasoning-delta`, `reasoning-end` |
| a call of a view tool | `tool-input-available` (`toolCallId`, `toolName`, `input`); then `tool-output-available` (`toolCallId`, `output`), or `tool-output-error` |
| a view applied through `apply_view` or `undo_view` | `data-view` (§4), right after that tool's output |
| a harness error | `error` (`errorText`) |
| end of turn | `finish-step`, `finish` |

The tool names are the bare ones from §4 (`apply_view`), not the harness's prefixed form
(`mcp__view__apply_view`).

## 4. View tools (MCP)

**Endpoint.** `POST /visualizer/mcp/{sessionId}`, outside `/visualizer/api`. It speaks MCP over streamable
HTTP, JSON-RPC 2.0, protocol version `2025-06-18`, and answers in plain JSON (no SSE needed). It supports
`initialize`, `notifications/initialized`, `ping`, `tools/list` and `tools/call`. A request without the
session's bearer secret gets HTTP 401. The harness reaches the endpoint at
`http://127.0.0.1:<the service's own port>`: the service adds that to its allowed hosts for this route,
including inside the container, where the published port and the internal port differ.

Each tool's result is a `content` list with one `text` item holding JSON. Results also carry
`structuredContent` with the same JSON. A failure is `isError: true`, with `{"error": code, "message",
"issues"?}`.

| Tool | Input | Result |
| --- | --- | --- |
| `get_current_view` | `{}` | `{owner, spec, counts, nodes: [{id, kind, label, level, category, graph, members, context}], edges: [{id, source, target, kind, type, label}], picked: [ids]}`. `category` is the card's gUFO type name, chosen as on the canvas. Edges are capped at 400. |
| `get_catalog` | `{}` | `{owner, graphs: [{path, level, release, own}], levels, presets}`: the owner's graphs and the graphs it may read as context. |
| `search_concepts` | `{query, graphs?: [path], limit?: ≤50}` | `{hits: [{iri, label, level, category, graph, definition}]}`. `graphs` defaults to the current scope. |
| `describe_concept` | `{iri}` | `{iri, labels, definitions, level, category, graphs, outgoing: [{type, kind, target, targetLabel}], incoming: [...]}`, up to 100 links each way. |
| `preview_view` | `{spec}` | Compiles the spec strictly, without applying it: `{ok: true, counts, shown: n}`, or `{ok: false, issues}`, or `{ok: false, error: "display-limit", count, limit}`. |
| `apply_view` | `{spec, summary}` | Compiles strictly. On success it makes the spec the session's current view, pushes it to the page (`data-view`) and remembers the previous one, then answers `{ok: true, counts}`. On failure it answers as `preview_view` does, and the page does not change. |
| `undo_view` | `{}` | Goes back to the previous view and pushes it the same way: `{ok: true}`, or `{ok: false}` when there is no earlier view. |

**Specs.** A spec is a full ViewSpec v1 (`schemas/views/view-spec.v1.json`), including
`presentation.arrangement`. The same checks apply as for the page:

- one version of each graph per scope;
- at most 200 nodes and 400 edges;
- groups of at most 500 members.

`summary` is one line saying what changed, and it is shown in the chat.

**Details** (`src/ontologizer/adapters/visualization/view_tools.py`, tests `tests/views/test_view_tools.py`):

- Protocol. `tools/call` checks the arguments against the tool's `inputSchema` (unknown tool or bad
  arguments: JSON-RPC `-32602`; unknown method `-32601`; a body that is not JSON `-32700`, a batch `-32600`).
  A spec's own content is checked by the compiler, not at the protocol level, so its issues come back as a
  tool result the agent can read. `tools/list` publishes the full ViewSpec schema as the `spec` input. A
  notification or a client response gets 202 with no body. The endpoint answers POST only; the bridge
  answers any other method with 405.
- Failures. Every failure is `isError: true` with `{"ok": false, "error", "message"}`, plus `issues`
  (`[{code, message, path}]`) for `invalid-spec` and `out-of-scope`, and `count` and `limit` for
  `display-limit`. The other codes are the HTTP API's (`engine-unavailable`, `unknown-owner`, …), plus
  `no-current-view` (the page sent no view), `unknown-entity` (`describe_concept`) and `nothing-to-undo`.
- Scope of `search_concepts` and `describe_concept`. The current view's scope by default. With `graphs`,
  the owner's own graphs among them are primary and the others context; with none of its own, the current
  primary graphs stay primary. `search_concepts` keeps only hits in the named graphs. A hit also carries
  `context`: the entity is in context graphs only. `describe_concept` gives each link's relationship
  `id` (for summaries) and `outgoingTotal`, `incomingTotal`.
- `category`. On a node of `get_current_view`, the canvas's own value: the node's markers named by the
  first gUFO class in the canvas's display order (`CATEGORY_ORDER` in `web/visualizer/src/lanes.ts`, ported
  and tested for equality), an area its members' shared value, else `mixed`; no marker is `other`. For
  `search_concepts` and `describe_concept`, as a card shows it with `class-inheritance` selected.
- `get_current_view` uses the page's compilation when the service still holds it for the same spec,
  else compiles the spec tolerantly (it is what the page shows). A node lists at most 50 member IRIs and
  carries `memberCount`; `edgesOmitted` counts edges past 400. `get_catalog` gives `presets: ["domain"]`,
  the values of `presentation.preset`.
- `preview_view` and `apply_view` compile on the current view's snapshot when its graphs are the same, else
  read a new one. `apply_view` answers `{ok, counts, viewEventId}`; `undo_view` `{ok, viewEventId,
  undoOf}`. Undo's `data-view` carries the restored spec, the summary `Undo: <summary>`, and in `undoOf`
  the id of the event it undoes. The undo history is the agent's applies in this session; the page's own
  edits between turns replace `current` but not the history.
- The session (`ViewSession`): `owner`, `id`, `secret` (`secrets.token_urlsafe(32)`, compared in constant
  time, never in `repr`), `current`, `history`, and `events`, a `queue.Queue` of `data-view` payloads that
  the bridge drains into the stream. The bridge calls `set_current(view)` with the request's `view` before a
  turn. The system prompt for the harness is `adapters/visualization/agent_instructions.md`.

**`data-view` chunk.**

```json
{"type": "data-view", "id": "<viewEventId>", "data": {"spec": {}, "summary": "...", "undoOf": null}}
```

The page shows it as an entry with an undo button. It also applies the spec through its usual compile path,
reusing the snapshot when the scope is unchanged, and lays the view out again.

## 5. Sign-in

The service runs the sign-in command in a pseudo-terminal, with `HOME` pointing at the agent's state
directory (§6). It reads the command's output, finds the URL and any code, and exposes them through
`GET agent/logins/{id}`. When the command asks for a code to paste back, it accepts one through
`POST …/code`. It never returns or logs a token.

- Claude: `claude auth login --claudeai`.
- Codex: `codex login --device-auth`.

Signed-in state is whatever the CLI itself reports: `claude auth status` and `codex login status`.

## 6. Where it runs

- **`uv run ontologizer visualizer`, on the host.** The host's own `claude` and `codex` and their sign-in
  are used. The state directory is the user's `HOME`.
- **`make viz-up`, in the container.**
  - The image installs both CLIs at pinned versions.
  - `HOME` for the harnesses is a named volume, `visualizer-agent-home`, mounted at `/agent-home`. Sign-in
    survives rebuilds there.
  - The container's root filesystem stays read-only.
  - The harnesses need the network for their model APIs; nothing else is opened.
