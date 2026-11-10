"""The visualizer's agent: the harness port (docs/visualizer-agent.md).

A harness is a coding-agent CLI (Claude Code, Codex) that the visualizer runs as a subprocess, one
process per chat turn, with the view tools as its only tools. An adapter knows one CLI: the command
line of a turn, how to read its JSON events as AI SDK UI message chunks, and its sign-in and status
commands. It starts nothing itself; the service (`adapters/visualization/agent.py`) runs what it
describes, so that time limits, stop and the secret's handling are the same for every harness.

Adapters are chosen by name in the hub's `visualizer.agent.harnesses` and resolved through the
entry-point group `ontologizer.view_agent`. The agent is off unless `visualizer.agent.enabled` is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# The view tools of contract §4: the only tools a harness may call.
VIEW_TOOLS = ("get_current_view", "get_catalog", "search_concepts", "describe_concept",
              "preview_view", "apply_view", "undo_view")
MCP_SERVER = "view"


class HarnessError(RuntimeError):
    """A harness cannot run: not installed, or its configuration is unusable. Names no secret."""


@dataclass(frozen=True)
class Turn:
    """What one turn needs. `secret` is the MCP endpoint's bearer: it reaches the harness only through
    a file of mode 0600 or the environment, never its command line."""
    prompt: str
    mcp_url: str
    secret: str
    instructions: str
    resume: str | None                   # the harness's own conversation id, from an earlier turn
    workdir: Path                        # an empty private directory: the harness's working directory
    scratch: Path                        # a private directory for this turn's files (removed after it)
    model: str | None = None             # this turn's model: None the adapter's configured one, "default" no flag
    effort: str | None = None            # the same for the effort, where the harness has one


@dataclass(frozen=True)
class Launch:
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)      # added to the service's harness environment
    stdin: bytes = b""
    close_stdin: bool = True             # False: stdin stays open until the converter says the turn ended


@dataclass(frozen=True)
class LoginScreen:
    """What a sign-in command has shown so far, read from its terminal output."""
    url: str | None = None
    user_code: str | None = None         # a code the user types on the provider's page (device flow)
    wants_code: bool = False             # the command waits for a code pasted back
    failure: str | None = None           # the command's own failure line, when it printed one


@dataclass(frozen=True)
class Choices:
    """What the page may choose for a harness (docs/visualizer-agent.md §2, §7): models as `{id, label}`, the
    efforts, and the defaults from the hub's config. The id `default` is the harness's own choice (no flag).
    The service accepts only these ids, so nothing else reaches a command line."""
    models: tuple[dict, ...]
    default_model: str | None
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None


class Converter(Protocol):
    """Reads one turn's JSON events. Every method returns AI SDK UI message chunks (`uiMessageChunkSchema`
    of ai@7.0.153) for the middle of the turn: the service itself sends start, start-step, data-view,
    finish-step, finish and abort."""

    resume_id: str | None                # the conversation id to resume on the next turn, once known
    done: bool                           # the harness reported the end of the turn
    model: str | None                    # the model the harness says answers, once it has said it (else None)

    def feed(self, event: dict) -> list[dict]: ...

    def close(self) -> list[dict]: ...   # the event stream ended: close what is still open


class Harness(Protocol):
    name: str
    reports_model: bool                  # its converter learns the answering model early in the turn

    def choices(self) -> Choices: ...

    def executable(self, path: str | None = None) -> str | None: ...   # the CLI on PATH `path`, or None

    def version_argv(self) -> tuple[str, ...]: ...

    def status_argv(self) -> tuple[str, ...]: ...

    def signed_in(self, returncode: int, output: str) -> bool: ...

    def login_argv(self) -> tuple[str, ...]: ...

    def read_login(self, screen: str) -> LoginScreen: ...      # `screen`: the output so far, ANSI removed

    def launch(self, turn: Turn) -> Launch: ...

    def converter(self) -> Converter: ...
