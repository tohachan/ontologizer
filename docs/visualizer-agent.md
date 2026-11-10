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
  than the view tools. The service starts each harness so (exact command lines in §3):
  - **Claude** (`adapters/visualization/harness_claude.py`): `--tools ""` (no built-in tool: no Bash, Read,
    Edit, WebFetch, Task…), `--strict-mcp-config` with the view server only, `--allowedTools` naming the
    seven view tools, `--permission-mode dontAsk` (anything else is refused, never asked),
    `--setting-sources ""` (no user or project settings, hooks or plugins) and `--system-prompt` (the
    instructions replace the coding prompt and any CLAUDE.md). Checked on 2.1.322: the session's tool list is
    exactly the `mcp__view__*` tools, and asked to run `ls` or read a file it has nothing to do it with. What
    remains is Claude's built-in plugins (telemetry, privacy) and its list of subagents, which it cannot
    start without the Task tool.
  - **Codex** (`harness_codex.py`): a read-only sandbox in an empty temporary directory,
    `approval_policy="never"`, `--ignore-user-config` and `--ignore-rules` (none of the user's MCP servers,
    hooks or profiles), the view server as its only MCP server, web search off, and these features
    disabled: `shell_tool`, `unified_exec`, `unified_exec_tty`, `shell_snapshot`, `apps`, `plugins`,
    `remote_plugin`, `hooks`, `image_generation`, `browser_use`, `browser_use_external`, `computer_use`,
    `in_app_browser`, `goals`, `view_image`, `sleep_tool`, `memories`, `multi_agent`, `multi_agent_v2`,
    `tool_suggest`, `skill_mcp_dependency_install`, `skill_search`, `workspace_dependencies`. Codex 0.166
    cannot be stripped of every tool of its own. What remains, checked by asking it to try each:
    - `functions.exec`, its code mode: JavaScript in which Codex calls the MCP tools. It has no `require`,
      no `fetch` and no file access. With `code_mode_host` off Codex cannot call MCP tools at all, so it
      stays on.
    - `apply_patch`: refused by the read-only sandbox ("writing is blocked by read-only sandbox").
    - `collaboration.*` sub-agents: they run with the same configuration.
    - `request_user_input`: there is no user in `codex exec`.
    - a clock, and the MCP resource listing of the view server, which has none.
- Harness processes get a reduced environment: `PATH`, locale, proxy and CA variables, and `HOME`. No API key
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`…), no engine token and nothing else of the service's environment
  reaches them, so they use the subscription they are signed in with. With the user's own HOME (§6),
  `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and the XDG directories pass too.
- The service never sends credentials to the page, never logs them, and never puts them in a command line.
  The session's secret reaches Claude in its MCP config, a file of mode 0600 in a private directory removed
  after the turn, and Codex in an environment variable (`ONTOLOGIZER_VIEW_MCP_TOKEN`) that its MCP config
  names (`bearer_token_env_var`). A harness's error text sent to the page has long token-like strings cut out.
- A session is bound to one tab by a random id, plus a secret the MCP endpoint requires
  (`Authorization: Bearer <secret>`). The secret goes only to the harness process, through its MCP config.
  The MCP route refuses any request with an `Origin` header: it is not for pages.
- The service never starts a harness unless the hub enables the agent. The setting is
  `visualizer.agent.enabled: true` in the hub's config, usually `ontologizer.local.yaml`; it is off by
  default. With the agent off, every `/agent` route and the MCP route answer HTTP 501 with the code
  `agent-disabled`.
- Limits: one turn at a time per session (a second answers 409 `turn-in-progress`), 180 s per turn (then
  the process is killed and the stream ends with an `error`), sessions idle for 30 min are ended, at most
  8 sessions (the least recently used idle one is ended for a new one). A closed tab ends its turn: the
  stream sends an SSE comment every 5 s while the harness is quiet, and a failed write kills the process.
  The settings `visualizer.agent.turn_timeout`, `idle_timeout`, `max_sessions` and `login_timeout` change them.

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

The routes live in `Visualizer._agent_route` (app.py) and the bridge in `adapters/visualization/agent.py`.
Further answers:

- `POST agent/sessions` checks the owner as every route does (404 `unknown-owner`); an unknown harness is
  404 `unknown-harness`, one not installed 409 `harness-not-installed`.
- `POST …/chat` answers 409 `turn-in-progress` while a turn runs, 404 `unknown-session` for an ended or
  expired session. The service reads only the last user message's text parts (at most 8000 characters), so
  the page may send just that message (`prepareSendMessagesRequest`); the body limit is the service's 1 MiB.
- `POST …/code` answers 409 `login-not-waiting` unless the sign-in is in `enter-code`; the code has no spaces.
- Harnesses are adapters chosen by name: `visualizer.agent.harnesses` (default `[claude, codex]`), resolved in
  the entry-point group `ontologizer.view_agent` (port `ports/view_agent.py`). Options per harness go under
  `visualizer.agent.<name>`: `command`, `model` (and `effort` for Claude); for Codex also `config` (more
  `-c key=value`, never a secret), `disable` (the feature list) and `env_passthrough` (variable names to pass).

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

What the service sends, checked against `uiMessageChunkSchema` and `readUIMessageStream` of `ai@7.0.153`
on real runs of both harnesses:

- One step per turn: `start`, `start-step`, …, `finish-step`, `finish` (`finishReason` `stop` or `error`).
  A stopped turn ends with `abort` (`reason: "stopped"`) and no `finish`. Every stream ends with `[DONE]`.
- Tool chunks carry `providerExecuted: true` (the service ran the tool; the page has nothing to execute).
  They are static tool parts, so the page sees them as `tool-<name>` parts (`tool-apply_view`).
- `data-view` chunks come from the view session's queue, drained after each tool output and once more at
  the end of the turn, so a view always follows the output of the call that made it.
- Lines starting with `:` (`: ping`) are SSE comments; the AI SDK's parser skips them.

**The harness command lines.** One process per turn, in the session's empty working directory, with the
reduced environment of §1.

Claude (2.1.322):

```
claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
       --mcp-config <turn dir>/mcp.json --strict-mcp-config
       --tools "" --allowedTools mcp__view__get_current_view,mcp__view__get_catalog,…,mcp__view__undo_view
       --permission-mode dontAsk --setting-sources "" --disable-slash-commands --no-chrome
       --system-prompt <instructions> [--model M] [--effort E] [--resume <session id>]
```

The user message goes to stdin as one stream-json line; stdin closes after the `result` event. `mcp.json` is
`{"mcpServers": {"view": {"type": "http", "url": …, "headers": {"Authorization": "Bearer …"}}}}`. Text and
thinking stream from the `stream_event` deltas; tool calls come from the complete `assistant` messages,
tool results from the `user` messages; `result` ends the turn (an error result becomes an `error` chunk).
The session id of `system/init` is the `--resume` of the next turn. Claude 2.1.322 sends the MCP method
`server/discover` before `initialize`; the endpoint may answer it with a JSON-RPC error. It also tries a
`GET` on the endpoint (an SSE stream), which answers 405.

Codex (0.166.0), the prompt on stdin:

```
codex exec [resume <thread id>] --json --skip-git-repo-check --ignore-user-config --ignore-rules
           [-s read-only -C <working dir>]          (first turn; resume takes neither)
           -c sandbox_mode="read-only" -c approval_policy="never" -c web_search="disabled"
           -c mcp_servers.view.url="http://127.0.0.1:<port>/visualizer/mcp/<session>"
           -c mcp_servers.view.bearer_token_env_var="ONTOLOGIZER_VIEW_MCP_TOKEN"
           -c mcp_servers.view.default_tools_approval_mode="approve"
           -c mcp_servers.view.enabled_tools=[…the seven view tools…]
           -c developer_instructions="<instructions>"  --disable <each feature of §1> [-m M] -
```

`default_tools_approval_mode="approve"` is needed: with `approval_policy="never"` Codex otherwise refuses
every MCP call ("requires approval"). Codex gives each message and reasoning summary whole on
`item.completed`; an `mcp_tool_call` opens on `item.started` and closes on `item.completed` (failed: a
`tool-output-error`). `thread.started` gives the thread id for `exec resume`; `turn.completed` ends the
turn, `turn.failed` is an `error`. A `command_execution`, `file_change` or `web_search` item, not expected
with those tools off, is shown as a tool call (`shell`, …) rather than hidden.

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

**`data-view` chunk.**

```json
{"type": "data-view", "id": "<viewEventId>", "data": {"spec": {}, "summary": "...", "undoOf": null}}
```

The page shows it as an entry with an undo button. It also applies the spec through its usual compile path,
reusing the snapshot when the scope is unchanged, and lays the view out again.

## 5. Sign-in

The service runs the sign-in command in a pseudo-terminal (`pty.openpty`, 1000 columns so that the URL is
never wrapped, echo off so that a pasted code is not printed back), with the harnesses' `HOME` (§6). It
reads the command's output without its escape sequences, finds the URL and any code, and exposes them
through `GET agent/logins/{id}`. When the command asks for a code to paste back, it accepts one through
`POST …/code` and writes it to the terminal with a carriage return. It never returns or logs a token, and
it keeps the output only while the sign-in runs. Both flows work headless (checked with an empty HOME and
no browser): each prints its URL when no browser opens.

- **Claude**: `claude auth login --claudeai`. It prints `Opening browser to sign in…`, then
  `If the browser didn't open, visit: <url>` (the URL also as an OSC 8 hyperlink), then waits at
  `Paste code here if prompted > `. The URL's redirect is the provider's code page (`code=true`), which
  shows the code to paste after sign-in. A wrong code prints
  `Login failed: That code wasn't accepted. …` and the command exits.
  States: `starting` → `enter-code` (with `url`) → after the paste `starting` (`message: "checking the
  code"`) → `done` when the command exits 0, else `failed` with its `Login failed…` line.
- **Codex**: `codex login --device-auth`. It prints `1. Open this link in your browser and sign in to your
  account` and the URL (`https://auth.openai.com/codex/device`), then `2. Enter this one-time code (expires
  in 15 minutes)` and the code (`XXXX-XXXXX`), and polls; nothing is pasted back.
  States: `starting` → `show-code` (with `url` and `userCode`) → `done` when it exits 0, else `failed`.
- `open-url` (a URL and nothing else to do) is for a flow that prints neither prompt.
- A sign-in fails after 15 min (`the sign-in timed out`); `DELETE` cancels it (`failed`, `cancelled`), and a
  new sign-in of the same harness cancels the one running. A finished sign-in is answered for 10 min.

Signed-in state is whatever the CLI itself reports: `claude auth status --json` (exit 0 and
`"loggedIn": true`) and `codex login status` (exit 0, not `Not logged in`), run with the same HOME and
environment as the turns, cached for 15 s and refreshed when a sign-in ends. Nothing else of their output
is used. `claude setup-token` is never used.

## 6. Where it runs

- **`uv run ontologizer visualizer`, on the host.** The host's own `claude` and `codex` and their sign-in
  are used. The state directory is the user's `HOME`, unless `$VISUALIZER_AGENT_HOME` or
  `visualizer.agent.home` names another one. Claude keeps each chat's transcript under that HOME's
  `.claude/projects/`, Codex under `.codex/sessions/`; the working directories are temporary and empty.
  With `--ignore-user-config` Codex reads no `config.toml`: a machine whose Codex needs settings from it
  (a custom model provider) gives them as `visualizer.agent.codex.config`.
- **`make viz-up`, in the container.**
  - The image installs both CLIs at pinned versions, `@anthropic-ai/claude-code@2.1.322` and
    `@openai/codex@0.166.0`, from `deps/visualizer/agent/package-lock.json` (npm checksums), in a Node stage.
    Only their native binaries reach the final image (`/opt/claude`, `/opt/codex` with Codex's
    `codex-code-mode-host` and `rg`), so it has no Node.js. The image is about 1.4 GB.
  - `HOME` for the harnesses is a named volume, `visualizer-agent-home`, mounted at `/agent-home`
    (`VISUALIZER_AGENT_HOME`). Sign-in survives rebuilds there; `viz.sh down` keeps it. `make viz-up` carries
    the hub's `visualizer.agent` into the container's settings, without `home`.
  - The container's root filesystem stays read-only; the harnesses' temporary files go to the `/tmp` tmpfs.
  - The harnesses need the network for their model APIs; nothing else is opened.
- **The MCP address.** The harness reaches the service at `http://127.0.0.1:<the port it listens on>`, so
  that Host is accepted on the MCP route in addition to the allowed hosts: inside the container it is 8090,
  whatever port is published. A service bound to one non-loopback address (`--host 192.0.2.1`) is not
  reachable there; bind 127.0.0.1 or 0.0.0.0.
