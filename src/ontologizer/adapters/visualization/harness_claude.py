"""Claude Code as the visualizer agent's harness (docs/visualizer-agent.md §1, §3, §5).

One turn is one `claude -p` process (checked against Claude Code 2.1.322):

    claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
           --mcp-config <scratch>/mcp.json --strict-mcp-config
           --tools "" --allowedTools mcp__view__get_current_view,… --permission-mode dontAsk
           --setting-sources "" --disable-slash-commands --no-chrome
           --system-prompt <instructions> [--model M] [--effort E] [--resume <session id>]

`--tools ""` removes every built-in tool (Bash, Read, Edit, WebFetch, Task…), `--strict-mcp-config`
every MCP server but the view's, `--allowedTools` lets the view tools run without a prompt and
`dontAsk` refuses anything else. `--setting-sources ""` keeps the user's settings, hooks and plugins
out, and `--system-prompt` replaces the coding prompt and the user's CLAUDE.md. The MCP config,
which holds the bearer secret, is a file of mode 0600 in the turn's private directory.

Sign-in is `claude auth login --claudeai`: it prints a URL and waits at `Paste code here if prompted >`
for the code the provider's page shows after sign-in.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from ontologizer.adapters.visualization.harness_base import bare_tool_name, tool_output, which
from ontologizer.ports.view_agent import MCP_SERVER, VIEW_TOOLS, Launch, LoginScreen, Turn

_URL = re.compile(r"https://\S+")


class ClaudeConverter:
    """Claude's stream-json events → UI message chunks. Text and thinking stream from the partial
    `stream_event`s; tool calls come from the complete `assistant` messages (their input whole), tool
    results from the `user` messages that carry them."""

    def __init__(self):
        self.resume_id: str | None = None
        self.done = False
        self._nonce = uuid.uuid4().hex[:8]
        self._message: str | None = None
        self._streamed: set[str] = set()          # messages whose text arrived as stream events
        self._blocks: dict[int, list] = {}         # index → [kind, chunk id, started]
        self._calls: set[str] = set()              # tool calls announced to the page

    def _id(self, *parts) -> str:
        return "-".join((self._nonce, *map(str, parts)))

    def feed(self, event: dict) -> list[dict]:
        kind = event.get("type")
        if event.get("parent_tool_use_id"):           # a subagent's events (there is no tool to start one)
            return []
        if kind == "system" and event.get("subtype") == "init":
            self.resume_id = event.get("session_id") or self.resume_id
            return []
        if kind == "stream_event":
            return self._stream(event.get("event") or {})
        if kind == "assistant":
            return self._assistant(event.get("message") or {})
        if kind == "user":
            return self._results(event.get("message") or {})
        if kind == "result":
            self.done = True
            self.resume_id = event.get("session_id") or self.resume_id
            if event.get("is_error") or event.get("subtype") != "success":
                errors = event.get("errors") or []
                text = event.get("result") or (errors[0] if errors and isinstance(errors[0], str) else None)
                return [{"type": "error", "errorText": str(text or f"the turn failed ({event.get('subtype')})")[:500]}]
        return []

    def _stream(self, ev: dict) -> list[dict]:
        t = ev.get("type")
        if t == "message_start":
            self._message = (ev.get("message") or {}).get("id") or uuid.uuid4().hex
            self._streamed.add(self._message)
            self._blocks = {}
            return []
        index = ev.get("index")
        if t == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "text":
                cid = self._id(self._message, index)
                self._blocks[index] = ["text", cid, True]
                return [{"type": "text-start", "id": cid}]
            if block.get("type") == "thinking":
                self._blocks[index] = ["reasoning", self._id(self._message, index), False]
            return []
        b = self._blocks.get(index)
        if b is None:
            return []
        if t == "content_block_delta":
            d = ev.get("delta") or {}
            if b[0] == "text" and d.get("type") == "text_delta" and d.get("text"):
                return [{"type": "text-delta", "id": b[1], "delta": d["text"]}]
            if b[0] == "reasoning" and d.get("type") == "thinking_delta" and d.get("thinking"):
                out = [] if b[2] else [{"type": "reasoning-start", "id": b[1]}]
                b[2] = True
                return out + [{"type": "reasoning-delta", "id": b[1], "delta": d["thinking"]}]
            return []
        if t == "content_block_stop":
            self._blocks.pop(index)
            if b[2]:
                return [{"type": f"{b[0]}-end", "id": b[1]}]
        return []

    def _assistant(self, msg: dict) -> list[dict]:
        out = []
        streamed = msg.get("id") in self._streamed
        for i, block in enumerate(msg.get("content") or []):
            t = block.get("type")
            if t == "tool_use" and block.get("id") not in self._calls:
                self._calls.add(block["id"])
                out.append({"type": "tool-input-available", "toolCallId": block["id"],
                            "toolName": bare_tool_name(block.get("name", "")), "input": block.get("input") or {},
                            "providerExecuted": True})
            elif not streamed and t == "text" and block.get("text"):
                cid = self._id(msg.get("id"), "t", i)
                out += [{"type": "text-start", "id": cid}, {"type": "text-delta", "id": cid, "delta": block["text"]},
                        {"type": "text-end", "id": cid}]
            elif not streamed and t == "thinking" and block.get("thinking"):
                cid = self._id(msg.get("id"), "r", i)
                out += [{"type": "reasoning-start", "id": cid},
                        {"type": "reasoning-delta", "id": cid, "delta": block["thinking"]},
                        {"type": "reasoning-end", "id": cid}]
        return out

    def _results(self, msg: dict) -> list[dict]:
        out = []
        content = msg.get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in self._calls:
                continue
            result = tool_output(block.get("content"))
            if block.get("is_error"):
                text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                out.append({"type": "tool-output-error", "toolCallId": block["tool_use_id"], "errorText": text[:2000],
                            "providerExecuted": True})
            else:
                out.append({"type": "tool-output-available", "toolCallId": block["tool_use_id"], "output": result,
                            "providerExecuted": True})
        return out

    def close(self) -> list[dict]:
        out = [{"type": f"{b[0]}-end", "id": b[1]} for b in self._blocks.values() if b[2]]
        self._blocks = {}
        return out


class Claude:
    name = "claude"

    def __init__(self, command: str = "claude", model: str | None = None, effort: str | None = None):
        self.command, self.model, self.effort = command, model, effort

    def executable(self, path: str | None = None) -> str | None:
        return which(self.command, path if path is not None else os.environ.get("PATH"))

    def version_argv(self):
        return (self.command, "--version")

    def status_argv(self):
        return (self.command, "auth", "status", "--json")

    def signed_in(self, returncode: int, output: str) -> bool:
        try:
            return returncode == 0 and json.loads(output).get("loggedIn") is True
        except (ValueError, AttributeError):
            return False

    def login_argv(self):
        return (self.command, "auth", "login", "--claudeai")

    def read_login(self, screen: str) -> LoginScreen:
        url = None
        at = screen.find("visit:")
        hit = _URL.search(screen, at if at >= 0 else 0)
        if hit:
            url = hit.group(0)
        failure = next((ln.strip() for ln in screen.splitlines() if ln.strip().startswith("Login failed")), None)
        return LoginScreen(url=url, wants_code="Paste code here" in screen, failure=failure)

    def launch(self, turn: Turn) -> Launch:
        config = Path(turn.scratch) / "mcp.json"
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"mcpServers": {MCP_SERVER: {"type": "http", "url": turn.mcp_url,
                                                   "headers": {"Authorization": f"Bearer {turn.secret}"}}}}, f)
        argv = [self.command, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages",
                "--mcp-config", str(config), "--strict-mcp-config",
                "--tools", "", "--allowedTools", ",".join(f"mcp__{MCP_SERVER}__{t}" for t in VIEW_TOOLS),
                "--permission-mode", "dontAsk", "--setting-sources", "", "--disable-slash-commands", "--no-chrome",
                "--system-prompt", turn.instructions]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if turn.resume:
            argv += ["--resume", turn.resume]
        message = {"type": "user", "message": {"role": "user", "content": turn.prompt}}
        return Launch(tuple(argv), stdin=(json.dumps(message, ensure_ascii=False) + "\n").encode(), close_stdin=False)

    def converter(self) -> ClaudeConverter:
        return ClaudeConverter()


def factory(conf: dict, base=None) -> Claude:
    return Claude(command=str(conf.get("command", "claude")), model=conf.get("model"), effort=conf.get("effort"))
