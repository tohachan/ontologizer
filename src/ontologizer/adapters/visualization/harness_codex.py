"""Codex as the visualizer agent's harness (docs/visualizer-agent.md §1, §3, §5).

One turn is one `codex exec` process (checked against codex-cli 0.166.0); later turns are
`codex exec resume <thread id> …` with the same options, the prompt on stdin (`-`):

    codex exec [resume <thread>] --json --skip-git-repo-check --ignore-user-config --ignore-rules
               [-s read-only -C <empty dir>]                       (first turn: resume takes neither)
               -c sandbox_mode="read-only" -c approval_policy="never" -c web_search="disabled"
               -c mcp_servers.view.url="http://127.0.0.1:<port>/visualizer/mcp/<session>"
               -c mcp_servers.view.bearer_token_env_var="ONTOLOGIZER_VIEW_MCP_TOKEN"
               -c mcp_servers.view.default_tools_approval_mode="approve"
               -c mcp_servers.view.enabled_tools=[…the view tools…]
               -c developer_instructions="<instructions>"
               --disable shell_tool --disable unified_exec … (DISABLED below) -

The bearer secret is only in the environment variable the MCP config names. `--ignore-user-config`
keeps the user's config.toml out (its MCP servers, hooks, profiles); sign-in still comes from
CODEX_HOME. With `approval_policy="never"` an MCP tool needs `default_tools_approval_mode="approve"`,
else every call is refused. What Codex keeps (docs/visualizer-agent.md §1): its code-mode `exec`
(JavaScript without `require`, `fetch` or files; Codex calls MCP tools through it), `apply_patch`
(refused by the read-only sandbox), sub-agents with the same limits, `request_user_input` (no user in
exec), a clock and the MCP resource listing of the view server.

Sign-in is `codex login --device-auth`: it prints a URL and a one-time code, and polls until the code is
entered on that page; nothing is pasted back.
"""

from __future__ import annotations

import json
import os
import re
import uuid

from ontologizer.adapters.visualization.harness_base import bare_tool_name, toml_value, tool_output, which
from ontologizer.ports.view_agent import MCP_SERVER, VIEW_TOOLS, Launch, LoginScreen, Turn

TOKEN_ENV = "ONTOLOGIZER_VIEW_MCP_TOKEN"
# Features of codex 0.166 that give the model tools beyond the view server's (`codex features list`).
# `code_mode_host` stays on: with it off Codex cannot call MCP tools at all.
DISABLED = ("shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "apps", "plugins", "remote_plugin",
            "hooks", "image_generation", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
            "goals", "view_image", "sleep_tool", "memories", "multi_agent", "multi_agent_v2", "tool_suggest",
            "skill_mcp_dependency_install", "skill_search", "workspace_dependencies")
_URL = re.compile(r"https://\S+")
_CODE = re.compile(r"\b([A-Z0-9]{4,}-[A-Z0-9]{4,})\b")
_SIDE_TOOLS = {"command_execution": "shell", "file_change": "file_change", "web_search": "web_search"}


class CodexConverter:
    """`codex exec --json` events → UI message chunks. Codex gives each message and reasoning summary
    whole, on `item.completed`; an MCP call opens on `item.started` and closes on `item.completed`."""

    def __init__(self):
        self.resume_id: str | None = None
        self.done = False
        self._nonce = uuid.uuid4().hex[:8]
        self._calls: set[str] = set()
        self._last_error: str | None = None

    def _id(self, item_id) -> str:
        return f"{self._nonce}-{item_id}"

    def feed(self, event: dict) -> list[dict]:
        t = event.get("type")
        if t == "thread.started":
            self.resume_id = event.get("thread_id") or self.resume_id
        elif t in ("item.started", "item.updated", "item.completed"):
            return self._item(event.get("item") or {}, t == "item.completed")
        elif t == "turn.completed":
            self.done = True
        elif t == "turn.failed":
            self.done = True
            message = (event.get("error") or {}).get("message") or self._last_error or "the turn failed"
            return [{"type": "error", "errorText": str(message)[:500]}]
        elif t == "error":                       # a stream error; fatal ones are followed by turn.failed
            self._last_error = event.get("message")
        return []

    def _call(self, item: dict, name: str, args) -> list[dict]:
        cid = self._id(item.get("id"))
        if cid in self._calls:
            return []
        self._calls.add(cid)
        return [{"type": "tool-input-available", "toolCallId": cid, "toolName": name, "input": args,
                 "providerExecuted": True}]

    def _item(self, item: dict, completed: bool) -> list[dict]:
        kind = item.get("type")
        cid = self._id(item.get("id"))
        if kind == "mcp_tool_call":
            out = self._call(item, bare_tool_name(item.get("tool", "")), item.get("arguments") or {})
            if not completed:
                return out
            result = item.get("result") or {}
            error = (item.get("error") or {}).get("message") if isinstance(item.get("error"), dict) else item.get("error")
            if error or item.get("status") == "failed" or result.get("is_error") or result.get("isError"):
                text = error or json.dumps(tool_output(result.get("content") or []), ensure_ascii=False)
                return out + [{"type": "tool-output-error", "toolCallId": cid, "errorText": str(text)[:2000],
                               "providerExecuted": True}]
            output = result.get("structured_content")
            if output is None:
                output = result.get("structuredContent")
            if output is None:
                output = tool_output(result.get("content") or [])
            return out + [{"type": "tool-output-available", "toolCallId": cid, "output": output,
                           "providerExecuted": True}]
        if kind in _SIDE_TOOLS:          # not expected with the tools disabled; shown, never hidden
            args = {k: item[k] for k in ("command", "changes", "query") if k in item}
            out = self._call(item, _SIDE_TOOLS[kind], args)
            if completed:
                output = {k: item[k] for k in ("status", "exit_code", "aggregated_output") if k in item}
                out.append({"type": "tool-output-available", "toolCallId": cid, "output": output,
                            "providerExecuted": True})
            return out
        if not completed:
            return []
        if kind == "agent_message" and item.get("text"):
            return [{"type": "text-start", "id": cid}, {"type": "text-delta", "id": cid, "delta": item["text"]},
                    {"type": "text-end", "id": cid}]
        if kind == "reasoning" and item.get("text"):
            return [{"type": "reasoning-start", "id": cid}, {"type": "reasoning-delta", "id": cid, "delta": item["text"]},
                    {"type": "reasoning-end", "id": cid}]
        return []                                # error items are warnings (e.g. an ignored setting)

    def close(self) -> list[dict]:
        return []


class Codex:
    name = "codex"

    def __init__(self, command: str = "codex", model: str | None = None, disable=DISABLED, config=None,
                 env_passthrough=()):
        self.command, self.model = command, model
        self.disable = tuple(disable)
        self.config = dict(config or {})          # extra `-c key=value` (per machine; never a secret)
        self.env_passthrough = tuple(env_passthrough)

    def executable(self, path: str | None = None) -> str | None:
        return which(self.command, path if path is not None else os.environ.get("PATH"))

    def version_argv(self):
        return (self.command, "--version")

    def status_argv(self):
        return (self.command, "login", "status")

    def signed_in(self, returncode: int, output: str) -> bool:
        return returncode == 0 and "not logged in" not in output.lower()

    def login_argv(self):
        return (self.command, "login", "--device-auth")

    def read_login(self, screen: str) -> LoginScreen:
        at = screen.find("Open this link")
        url = _URL.search(screen, max(at, 0))
        code_at = screen.find("one-time code")
        code = _CODE.search(screen, code_at) if code_at >= 0 else None
        failure = next((ln.strip() for ln in screen.splitlines()
                        if ln.strip().lower().startswith(("error", "login failed"))), None)
        return LoginScreen(url=url.group(0) if url else None, user_code=code.group(1) if code else None,
                           failure=failure)

    def launch(self, turn: Turn) -> Launch:
        argv = [self.command, "exec"]
        if turn.resume:
            argv += ["resume", turn.resume]
        argv += ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
        if not turn.resume:
            argv += ["-s", "read-only", "-C", str(turn.workdir)]
        settings = {"sandbox_mode": "read-only", "approval_policy": "never", "web_search": "disabled",
                    f"mcp_servers.{MCP_SERVER}.url": turn.mcp_url,
                    f"mcp_servers.{MCP_SERVER}.bearer_token_env_var": TOKEN_ENV,
                    f"mcp_servers.{MCP_SERVER}.default_tools_approval_mode": "approve",
                    f"mcp_servers.{MCP_SERVER}.enabled_tools": list(VIEW_TOOLS),
                    "developer_instructions": turn.instructions,
                    **self.config}
        for k, v in settings.items():
            argv += ["-c", f"{k}={toml_value(v)}"]
        for f in self.disable:
            argv += ["--disable", f]
        if self.model:
            argv += ["-m", self.model]
        argv.append("-")
        env = {TOKEN_ENV: turn.secret}
        env.update({k: os.environ[k] for k in self.env_passthrough if k in os.environ})
        return Launch(tuple(argv), env=env, stdin=turn.prompt.encode())

    def converter(self) -> CodexConverter:
        return CodexConverter()


def factory(conf: dict, base=None) -> Codex:
    return Codex(command=str(conf.get("command", "codex")), model=conf.get("model"),
                 disable=conf.get("disable", DISABLED), config=conf.get("config"),
                 env_passthrough=conf.get("env_passthrough", ()))
