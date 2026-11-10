"""What the harness adapters share: tool names, tool output, terminal text."""

from __future__ import annotations

import json
import re
import shutil

from ontologizer.ports.view_agent import MCP_SERVER

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")        # OSC 8 hyperlinks: the URL also appears as text
_OTHER = re.compile(r"\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]")
_PREFIXES = (f"mcp__{MCP_SERVER}__", f"{MCP_SERVER}.", f"{MCP_SERVER}__")


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
