"""What the harness adapters share: tool names, tool output, terminal text."""

from __future__ import annotations

import json
import re
import shutil

from ontologizer.ports.view_agent import MCP_SERVER, Choices

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")        # OSC 8 hyperlinks: the URL also appears as text
_OTHER = re.compile(r"\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]")
_PREFIXES = (f"mcp__{MCP_SERVER}__", f"{MCP_SERVER}.", f"{MCP_SERVER}__")
# A model id or effort that may reach a command line: no leading dash, no space, nothing a TOML value or an
# option parser would read as more (`claude-opus-4-1`, `gpt-5.1-codex`, `claude-sonnet-4-5[1m]`).
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}")
DEFAULT = "default"                                             # the harness's own choice: no flag


def safe_id(v) -> str | None:
    return v if isinstance(v, str) and SAFE_ID.fullmatch(v) else None


def choices(builtin_models, builtin_efforts=(), models=None, efforts=None, model=None, effort=None) -> Choices:
    """The page's choices for one harness (docs/visualizer-agent.md §7): the config's `models` (ids, or
    `{id, label}`) when set, else the built-in list; the config's `efforts` likewise; the config's `model`
    and `effort` as the defaults. An id that is not safe is dropped; a configured default missing from its
    list is added to it."""
    listed: list[dict] = []
    for m in models if isinstance(models, (list, tuple)) and models else builtin_models:
        mid, label = (m.get("id"), m.get("label")) if isinstance(m, dict) else (m, None)
        if safe_id(mid) and mid not in {x["id"] for x in listed}:
            listed.append({"id": mid, "label": str(label) if label else "Default" if mid == DEFAULT else mid})
    ids = {x["id"] for x in listed}
    default_model = safe_id(model)
    if default_model and default_model not in ids:
        listed.append({"id": default_model, "label": default_model})
    if not default_model:
        default_model = DEFAULT if DEFAULT in ids else None
    ef = [e for e in (efforts if isinstance(efforts, (list, tuple)) else builtin_efforts) if safe_id(e)]
    default_effort = safe_id(effort)
    if default_effort and default_effort not in ef:
        ef.append(default_effort)
    return Choices(tuple(listed), default_model, tuple(dict.fromkeys(ef)), default_effort)


def bare_tool_name(name: str) -> str:
    """`mcp__view__apply_view` (Claude) or `view.apply_view` → `apply_view`."""
    for p in _PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


def tool_output(content):
    """An MCP tool result's text as the JSON it holds, else as text. `content` is a string or a list of
    content items."""
    if isinstance(content, list):
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    else:
        text = content if isinstance(content, str) else json.dumps(content)
    try:
        return json.loads(text)
    except ValueError:
        return text


def plain(screen: str) -> str:
    """Terminal output without escape sequences, with \\r\\n as \\n."""
    s = _OSC.sub("", screen)
    s = _CSI.sub("", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return _OTHER.sub("", s)


def which(command: str, path: str | None) -> str | None:
    return shutil.which(command, path=path)


def toml_value(v) -> str:
    """A value for `codex -c key=value`, which parses the value as TOML."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(f"no TOML form for {type(v).__name__}")
