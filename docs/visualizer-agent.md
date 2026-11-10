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
    view tools (`VIEW_TOOLS`), `--permission-mode dontAsk` (anything else is refused, never asked),
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
| `GET agent/harnesses` | — | `{"harnesses": [{"name": "claude" \| "codex", "installed": bool, "signedIn": bool, "version": str \| null, "models": [{"id", "label"}], "defaultModel": str \| null, "efforts": [str], "defaultEffort": str \| null}]}` |
| `POST agent/harnesses/{name}/login` | `{}` | `{"loginId"}`. Starts the sign-in (§5). |
| `GET agent/logins/{loginId}` | — | `{"state": "starting" \| "open-url" \| "enter-code" \| "show-code" \| "done" \| "failed", "url": str \| null, "userCode": str \| null, "message": str \| null}` |
| `POST agent/logins/{loginId}/code` | `{"code": str}` | `{"state"}`. For a flow that asks for a code to paste back. |
| `DELETE agent/logins/{loginId}` | — | `{}`. Cancels the sign-in. |
| `POST agent/sessions` | `{"harness": "claude" \| "codex"}` (an `owner` is accepted and ignored) | `{"sessionId"}` |
| `DELETE agent/sessions/{sessionId}` | — | `{}`. Ends the session and kills its process. |
| `POST agent/sessions/{sessionId}/chat` | the AI SDK chat request (§3) | An SSE stream (§3) |
| `POST agent/sessions/{sessionId}/stop` | `{}` | `{}`. Kills the turn in progress; the stream ends with an `abort` chunk. |

The harness used for a session is fixed when the session is created. Switching harness on the page ends
the session and starts a new one.

The routes live in `Visualizer._agent_route` (app.py) and the bridge in `adapters/visualization/agent.py`.
Further answers:

- `POST agent/sessions` binds no owner (ONTO-49): each chat request's `view.owner` names it. An unknown harness is
  404 `unknown-harness`, one not installed 409 `harness-not-installed`.
- `POST …/chat` answers 409 `turn-in-progress` while a turn runs, 404 `unknown-session` for an ended or
  expired session, 400 `unknown-model` or `unknown-effort` for an `agent` choice the harness does not list. The service reads only the last user message's text parts (at most 8000 characters), so
  the page may send just that message (`prepareSendMessagesRequest`); the body limit is the service's 1 MiB.
- `POST …/code` answers 409 `login-not-waiting` unless the sign-in is in `enter-code`; the code has no spaces.
- Harnesses are adapters chosen by name: `visualizer.agent.harnesses` (default `[claude, codex]`), resolved in
  the entry-point group `ontologizer.view_agent` (port `ports/view_agent.py`). Options per harness go under
  `visualizer.agent.<name>`: `command`, `model` and `models` (and `effort` and `efforts` for Claude); for Codex
  also `config` (more `-c key=value`, never a secret), `disable` (the feature list) and `env_passthrough`
  (variable names to pass).
- The choices of `GET agent/harnesses` (ONTO-47): `models` is the config's `models` (a list of ids, or of
  `{id, label}`) when set, else the built-in list: for Claude `default`, `opus`, `sonnet`, `haiku` (Claude's own
  aliases), with efforts `low`, `medium`, `high`; for Codex `default` alone, since the models an account may
  use differ (a machine lists its own). `defaultModel` is the config's `model`, else `default` when listed;
  `defaultEffort` the config's `effort`, else null. An id that does not match
  `[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}` is dropped, and a configured default missing from its list is
  added to it. `default` is the harness's own choice: no `--model` or `-m` flag.

## 3. The chat stream: AI SDK UI message stream

The page uses the AI SDK chat protocol: `ai@7.0.153` with `@ai-sdk/react@4.0.157`, optionally with
assistant-ui (`@assistant-ui/react@0.15.31`, `@assistant-ui/react-ai-sdk@1.4.20`) for the components.
Those are the pinned versions. The exact chunk names and fields are those of `uiMessageChunkSchema` in
`ai@7.0.153`. Both sides read them from that package, not from memory.

**Request.** `POST agent/sessions/{id}/chat`. The body is what the AI SDK's `DefaultChatTransport` sends:
`{"id", "messages": [UIMessage…], "trigger", "messageId"}`. The page adds `"view"` and `"agent"`:

```json
{"view": {"owner": "<the open view's owner, or null>", "spec": "<the ViewSpec of the open view, or null>",
          "compileId": "...", "picked": ["<node or edge id>"]},
 "agent": {"model": "haiku", "effort": "low"}}
```

`messageId` is present only on a regenerate. For a new message JSON drops it, so the body is
`{"view", "agent", "id", "messages", "trigger": "submit-message"}`, and `id` is the session id. With no
diagram on the page `view` is `{"owner": null, "spec": null, "compileId": null, "picked": []}`, and the chat
works all the same. `view.picked` holds the picked cards and links, which the page shows as context chips
above the input ("this one").

`agent` is the model and effort chosen in the Agent tab (§7). It applies to this turn and the following
ones; a key left out keeps the session's earlier choice, and with none the harness's configured default
applies. `default` or null is the harness's own choice (no flag). Only ids the harness lists are accepted, so
nothing else reaches its command line. A changed model keeps the conversation: Claude takes `--model` with
`--resume`, Codex `-m` with `exec resume` (both checked with `--help` on 2.1.322 and 0.166.0).

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
| the answering model is known | `message-metadata` (`messageMetadata: {"harness", "model"}`) |
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
- One `message-metadata` per turn, merged by the AI SDK into the assistant message's `metadata`. `model` is the
  one the harness reports, else the one requested, else null (the harness's default, unnamed):
  - Claude names it in `system/init` (`"model": "claude-haiku-…"`), so the chunk follows that event, before any
    text or tool chunk. A turn that ends before `system/init` gets the requested model at its end.
  - `codex exec --json` 0.166.0 names no model: `thread.started` carries only `thread_id`, `turn.started`
    nothing, and no item names one (checked on a real run). The chunk follows `start-step`, with the requested
    model.
  The page shows it under the answer, small and muted, or "default model" when it is null.

The page reads the stream through `useChat`:

- A tool chunk without `dynamic: true` becomes a part of type `tool-<name>`; with it, `dynamic-tool`.
  The page shows both the same way.
- A `tool-input-available` with no `tool-input-start` before it is enough.
- An `error` chunk ends the turn as failed and shows inline.
- After `stop`, the page marks tool calls still waiting for an output as stopped.

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
           -c mcp_servers.view.enabled_tools=[…the view tools…]
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
| `get_current_view` | `{}` | `{owner, spec, counts, nodes: [{id, kind, label, level, category, graph, members, context}], edges: [{id, source, target, kind, type, label}], picked: [ids]}`. `category` is the card's gUFO type name, chosen as on the canvas. Edges are capped at 400. With no diagram open: `{owner: null, spec: null, empty: true, nodes: [], edges: [], message}`, not a failure. |
| `get_catalog` | `{}` | `{owners: [{name, available, levels, graphs: [{path, level, role, release, own, concepts}]}], currentOwner, presets, note}`: every owner the hub registers. |
| `list_concepts` | `{graphs?, owners?, levels?, categories?, offset?, limit?: ≤500}` | `{total, offset, concepts: [{iri, label, level, category, graph}], nextOffset?}`, ordered by graph, then label, then IRI. Default limit 200. |
| `search_concepts` | `{queries: [str] (1..10), query?, graphs?, owners?, levels?, limit?: ≤50}` | `{results: [{query, total, hits: [{iri, label, level, category, graph, owner, match, definition, context?}]}]}`, one result per query, in order. `limit` is per query (default 10). |
| `describe_concept` | `{iri, graphs?}` | `{iri, label, owner, labels, definitions, level, category, status, graphs, outgoing: [{id, type, kind, target, targetLabel}], incoming: [...], outgoingTotal, incomingTotal}`, up to 100 links each way. |
| `preview_view` | `{owner?, spec}` | Compiles the spec strictly for the owner, without applying it: `{ok: true, owner, counts, shown: n}`, or `{ok: false, issues}`, or `{ok: false, error: "display-limit", count, limit}`. |
| `apply_view` | `{owner?, spec, summary?}` | Compiles strictly. On success it makes the spec the session's current view, pushes it to the page (`data-view`, with `owner`) and remembers the previous one, then answers `{ok: true, owner, counts, viewEventId}`. On failure it answers as `preview_view` does, and the page does not change. `summary` defaults to the spec's title. |
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
  answers any other method with 405. The harnesses may call exactly the tools of `VIEW_TOOLS`
  (`ports/view_agent.py`), which a test keeps equal to the tools listed.
- Failures. Every failure is `isError: true` with `{"ok": false, "error", "message"}`, plus `issues`
  (`[{code, message, path}]`) for `invalid-spec` and `out-of-scope`, and `count` and `limit` for
  `display-limit`. The other codes are the HTTP API's (`engine-unavailable`, `unknown-owner`, …), plus
  `bad-arguments`, `unknown-graph`, `two-versions`, `no-owner`, `unknown-entity`, `bad-summary` and
  `nothing-to-undo`. Every message ends with what to do next ("call get_catalog for the graph paths",
  "collapse groups, drop seeds…"); for the HTTP API's codes the tools append it (`NEXT` in view_tools.py).
  No tool fails because the page shows no view, or because its view has no primary graph.
- **Owners.** A session is not bound to an owner. The current view's owner is the request's `view.owner`;
  for a view sent without one, the one owner whose own graphs its `primaryGraphs` are. `preview_view` and
  `apply_view` compile for the `owner` given, else the owner of the spec's primary graphs, else the current
  view's owner; without any, they fail with `no-owner`. Compile still enforces the rules: primary graphs
  only of that owner, one version of each graph.
- **What search, listing and describe read** (the *index*). Every graph some available owner's catalog lists,
  read as a set of scopes, one per owner: that owner's own graphs as primary, the kit's graphs (`upper/gufo`,
  `kit/core`, `inferred/*`: graphs no available owner owns) as context. In the hub of 2026-11-10 every graph
  has at most one owning config; a graph several owners claimed would be read under the first by name.
  - **Version choice.** One version of each graph: the working version (the path without `@`), else, for a
    graph that has only releases, the newest release by version number. A release is read only when named in
    `graphs`; naming a release and its working version reads them in two scopes, never one. Release concepts
    have their own IRIs (`…/domain@0.2:x`), so the two never merge. A view that reads releases is searched
    by naming its graphs.
  - An entity found in several scopes is taken from the one holding its home graph (the graph whose term
    namespace its IRI is in). A record's `graph` is that home graph, else a primary graph it is in; its
    `owner` the owner of that graph (null for the kit's). `context: true` marks an entity only in context
    graphs.
  - Each scope's snapshot is read through `Visualizer.source` and indexed once; the index is cached for
    `Settings.index_ttl` (120 s). Filters (`graphs`, `owners`, `levels`, `categories`) apply to the index.
    A scope that cannot be read (`scope-too-large`) is reported in `unread` and the others answer; when none
    can be read the call fails.
  - `get_catalog` lists, under each available owner, its own graphs and the kit's graphs it may read. Each
    graph's `concepts` is the number of index records with that home graph; it is null for a version the
    index does not read by default (a release with a working version).
- **Matching** (`core/views/match.py`, also the page's `POST search`). A query is split into words
  (casefolded, accents removed). A concept matches when every word starts a word of its labels or
  definitions, in any order ("lease" does not find "release"); a word of five letters or more also matches
  through a crude stem (two letters off from six, one off at five), so "sessions" finds "Session" and
  "сессии" finds "Сессия". Ranking: `exact` (a label is the query), `label` (one label holds every word, then
  every stem), `text` (the words spread over labels and definitions); within a tier, a hit whose shown label
  matches too, then shorter labels, then label and IRI. Labels and definitions are chosen in `en`, then `ru`.
- `category`. On a node of `get_current_view`, the canvas's own value: the node's markers named by the
  first gUFO class in the canvas's display order (`CATEGORY_ORDER` in `web/visualizer/src/lanes.ts`, ported
  and tested for equality), an area its members' shared value, else `mixed`; no marker is `other`. For
  the index (`search_concepts`, `list_concepts`, `describe_concept`), as a card shows it with
  `class-inheritance` selected.
- `get_current_view` uses the page's compilation when the service still holds it for the same spec and
  owner, else compiles the spec tolerantly (it is what the page shows). A node lists at most 50 member IRIs
  and carries `memberCount`; `edgesOmitted` counts edges past 400. `get_catalog` gives `presets:
  ["domain"]`, the values of `presentation.preset`.
- `preview_view` and `apply_view` compile on the current view's snapshot when it is the same owner's and of
  the same graphs, else read a new one. `undo_view` answers `{ok, viewEventId, undoOf}`. Undo's `data-view`
  carries the restored spec and its owner, the summary `Undo: <summary>`, and in `undoOf` the id of the event
  it undoes. The undo history is the agent's applies in this session; the page's own edits between turns
  replace `current` but not the history.
- The session (`ViewSession`): `owner` (optional, unused since ONTO-49), `id`, `secret`
  (`secrets.token_urlsafe(32)`, compared in constant time, never in `repr`), `current` (`{owner, spec,
  compileId, picked}`), `history`, and `events`, a `queue.Queue` of `data-view` payloads that the bridge
  drains into the stream. The bridge calls `set_current(view)` with the request's `view` before a turn. The
  system prompt for the harness is `adapters/visualization/agent_instructions.md`: the order of work
  (current view, then `list_concepts` or one batched `search_concepts`, `describe_concept` only when needed,
  preview, apply with the owner), a budget of about 5–8 calls, English queries next to the user's words.

**`data-view` chunk.**

```json
{"type": "data-view", "id": "<viewEventId>", "data": {"spec": {}, "summary": "...", "undoOf": null, "owner": "<owner>"}}
```

The page shows it as an entry with an undo button. When `owner` differs from the page's, the page switches to
that owner, keeping the chat. It then applies the spec through its usual compile path, reusing the snapshot
when the scope is unchanged, and lays the view out again.

- If the spec has no `arrangement`, the page keeps its own.
- The `id` must be unique within a session: the page keys the entry, and the spec to undo to, on it.
- The entry's **Undo** is local. It re-applies the spec the page had just before that entry arrived, and
  does not call the agent. The agent learns of it from `view` on the next request.

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

## 7. Changes of 2026-11-10: model choice, findable concepts, no owner binding (ONTO-47, ONTO-48, ONTO-49)

These changes replace the earlier sections where the two disagree. The sections themselves are updated when
the cards land.

**Sessions are not bound to an owner (ONTO-49).**

- `POST agent/sessions` takes `{"harness"}`. An `owner` it is sent is accepted and ignored.
- The chat request's `view` carries the page's owner: `{"owner": str | null, "spec", "compileId", "picked"}`.
  `owner` and `spec` are null when the page shows no diagram.
- The tools read every owner's graphs:
  - `get_catalog` answers `{owners: [{name, available, levels, graphs: [{path, level, role, release, own, concepts}]}]}`.
  - Search and `list_concepts` cover every graph the hub knows, unless `graphs` narrows them.
- `preview_view` and `apply_view` take `{owner, spec, summary?}`. `owner` is the owner whose own graphs are the
  spec's primary graphs; without it, the tools take the current view's owner.
- The `data-view` chunk's data gains `owner`: `{"spec", "summary", "undoOf", "owner"}`. When `owner` differs
  from the page's, the page switches to that owner, keeping the chat, and then applies the spec.
- The Agent tab has no owner choice; the Settings tab keeps it.
- On the page (built): the owner select sits in the Settings tab, and the header shows the current owner as
  plain text. The session is created with `{"harness"}` and lives through any owner change; a fresh page with
  no owner chats as well. A `data-view` whose `owner` differs from the page's switches the page as choosing
  that owner in Settings does (its catalog and saved views, a new canvas), leaves the chat and the session
  alone, and then compiles the spec for that owner. The agent's views and undos are applied one after the
  other. Each entry's local **Undo** remembers the owner and spec before it (none, when no diagram was shown),
  so it goes back across an owner switch too.

Status: the service side has landed (§2, §4: sessions, the owner of a view and of an apply, the catalog
across owners, the index over every owner's graphs and its version choice). The page side is ONTO-49's
other half.

**Models (ONTO-47).**

- `GET agent/harnesses`: each harness gains
  `"models": [{"id", "label"}], "defaultModel": str | null, "efforts": [str], "defaultEffort": str | null`.
  - The list is the config's `visualizer.agent.<harness>.models` when set. Otherwise it is a built-in list:
    for Claude, `default`, `opus`, `sonnet` and `haiku`, with efforts `low`, `medium`, `high`; for Codex,
    `default`.
  - `default` means the harness's own choice: no `--model` or `-m` flag.
- The chat request gains `"agent": {"model": str | null, "effort": str | null}`. It applies to that turn and
  the following ones. A changed model keeps the conversation: Claude takes `--model` with `--resume`, and
  Codex takes `-m` with `exec resume`.
- After `start`, the stream sends `{"type": "message-metadata", "messageMetadata": {"harness", "model"}}`.
  `model` is the one the harness reports (Claude's `system/init`), else the one requested, else null. The
  page shows it under the answer.
- Built (§2, §3): the service checks `agent` against the listed choices; Codex reports no model, so its chunk
  carries the requested one. On the page, the Agent tab has a model select under the harness choice, and for
  Claude an effort select (`Default` sends null). Both are remembered per harness in `localStorage`
  (`visualizer.agent.<harness>.model`, `.effort`); a remembered id the service no longer lists falls back to
  `defaultModel`.

**Tools that find concepts in a few calls (ONTO-48).**

- `search_concepts {queries: [str] (1..10), graphs?, levels?, limit?}`. Each query matches its words in any
  order, without case, in labels and definitions. A label match ranks before a definition match. The answer
  is `{results: [{query, hits: [...], total}]}`. A single `query` string is still accepted.
- `list_concepts {graphs?, levels?, categories?, offset?, limit? (≤500)}` answers
  `{total, concepts: [{iri, label, level, category, graph}]}`.
- No tool fails because the page has no view, or because its view has no primary graph.
- Failures say what to do next.
- The instructions give a budget of about 5–8 calls per request, and prefer `list_concepts` and batched
  queries.

Status: landed, as §4 describes, with these additions: `owners` filters both tools; a hit carries `owner`
and `match` (`exact`, `label`, `text`); matching is also without accents and finds plurals and inflections
through a stem; the page's `POST search` matches the same way (`core/views/match.py`). Checked on the hub
of 2026-11-10 (4 available owners, about 1,600 concepts): `get_catalog` 2.9 s cold (it reads the index),
6.8 kB; later calls about 0.03 s. `list_concepts` of one owner's level about 8 kB; the whole of the largest
owner (meta-harness, 1,015 concepts) 3 pages, about 165 kB (some 41k tokens), so the instructions point to
`owners` and `levels`. The owner's request about agent sessions took 4 tool calls with Claude (sonnet), none
failed.
