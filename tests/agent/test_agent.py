"""The agent bridge (docs/visualizer-agent.md §1–§3, §5): converters on recorded harness events, the session
lifecycle with a fake harness process, the SSE stream, the MCP route, sign-in in a pseudo-terminal.
Hermetic: no harness CLI, no store, no network beyond 127.0.0.1."""

from __future__ import annotations

import http.client as http_client
import json
import os
import queue
import secrets
import stat
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from conftest import make_hub, make_study
from ontologizer.adapters.visualization import server
from ontologizer.adapters.visualization.agent import AgentService, AgentSettings, harness_env
from ontologizer.adapters.visualization.app import Settings, Visualizer
from ontologizer.adapters.visualization.harness_base import bare_tool_name, plain, toml_value
from ontologizer.adapters.visualization.harness_claude import Claude, ClaudeConverter
from ontologizer.adapters.visualization.harness_codex import DISABLED, TOKEN_ENV, Codex, CodexConverter
from ontologizer.core.views.scope import Owners
from ontologizer.ports.view_agent import VIEW_TOOLS, Turn

HERE = Path(__file__).parent
FIX = HERE.parent / "fixtures" / "agent"
FAKE = HERE / "fake_cli.py"
HOST = "127.0.0.1:8090"
# The required fields of each chunk type used, as uiMessageChunkSchema of ai@7.0.153 has them.
REQUIRED = {"start": set(), "start-step": set(), "finish-step": set(), "finish": set(), "abort": set(),
            "text-start": {"id"}, "text-delta": {"id", "delta"}, "text-end": {"id"},
            "reasoning-start": {"id"}, "reasoning-delta": {"id", "delta"}, "reasoning-end": {"id"},
            "tool-input-available": {"toolCallId", "toolName", "input"},
            "tool-output-available": {"toolCallId", "output"}, "tool-output-error": {"toolCallId", "errorText"},
            "error": {"errorText"}, "message-metadata": {"messageMetadata"}}


def feed(conv, name):
    out = []
    for line in (FIX / name).read_text().splitlines():
        out += conv.feed(json.loads(line))
    return out + conv.close()


def well_formed(chunks):
    """Every chunk is of a known type with its required fields; text and reasoning blocks open and close."""
    open_ = set()
    for c in chunks:
        t = c["type"]
        if t.startswith("data-"):
            assert "data" in c
            continue
        assert t in REQUIRED, t
        assert REQUIRED[t] <= set(c), c
        if t.endswith("-start") and t != "start":
            assert c["id"] not in open_
            open_.add(c["id"])
        elif t.endswith("-delta"):
            assert c["id"] in open_
        elif t.endswith("-end"):
            open_.remove(c["id"])
    assert not open_


# ---- converters --------------------------------------------------------------------------------
@pytest.mark.parametrize("conv,name,resume", [(ClaudeConverter, "claude-turn.jsonl", "session-1"),
                                              (CodexConverter, "codex-turn.jsonl", "thread-1")])
def test_a_recorded_turn_becomes_ui_chunks(conv, name, resume):
    c = conv()
    chunks = feed(c, name)
    well_formed(chunks)
    assert c.done and c.resume_id == resume
    tools = [(x["toolName"], x["input"]) for x in chunks if x["type"] == "tool-input-available"]
    assert tools == [("get_current_view", {}), ("apply_view", {"spec": {"id": "v2"}, "summary": "only Order"}),
                     ("apply_view", {"spec": {}, "summary": "fail"})]
    outcome = [x["type"] for x in chunks if x["type"].startswith("tool-output")]
    assert outcome == ["tool-output-available", "tool-output-available", "tool-output-error"]
    applied = [x for x in chunks if x["type"] == "tool-output-available"][1]
    assert applied["output"] == {"ok": True, "counts": {"nodes": 2, "edges": 1}}
    assert "invalid-spec" in [x for x in chunks if x["type"] == "tool-output-error"][0]["errorText"]
    calls = {x["toolCallId"] for x in chunks if x["type"] == "tool-input-available"}
    assert {x["toolCallId"] for x in chunks if x["type"].startswith("tool-output")} == calls
    text = "".join(x["delta"] for x in chunks if x["type"] == "text-delta")
    assert text.strip()


@pytest.mark.parametrize("conv,name,answer", [(ClaudeConverter, "claude-resume.jsonl", "only Order"),
                                              (CodexConverter, "codex-resume.jsonl", "only Order")])
def test_a_resumed_turn_keeps_the_conversation(conv, name, answer):
    c = conv()
    chunks = feed(c, name)
    well_formed(chunks)
    assert answer in "".join(x["delta"] for x in chunks if x["type"] == "text-delta")
    assert not [x for x in chunks if x["type"].startswith("tool")]


def test_claude_streams_text_once_and_ignores_the_repeat_in_the_full_message():
    c = ClaudeConverter()
    chunks = feed(c, "claude-turn.jsonl")
    full = [json.loads(ln) for ln in (FIX / "claude-turn.jsonl").read_text().splitlines()]
    final = next(b["text"] for e in reversed(full) if e["type"] == "assistant"
                 for b in e["message"]["content"] if b["type"] == "text")
    assert "".join(x["delta"] for x in chunks if x["type"] == "text-delta") == final


def test_claude_without_partial_messages_still_gives_text():
    c = ClaudeConverter()
    out = c.feed({"type": "assistant", "message": {"id": "m", "content": [
        {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Done."}]}})
    well_formed(out)
    assert [x["type"] for x in out] == ["reasoning-start", "reasoning-delta", "reasoning-end",
                                         "text-start", "text-delta", "text-end"]


def test_a_cut_stream_closes_its_open_blocks():
    c = ClaudeConverter()
    lines = (FIX / "claude-turn.jsonl").read_text().splitlines()
    cut = next(i for i, ln in enumerate(lines) if '"text_delta"' in ln) + 1
    out = []
    for ln in lines[:cut]:
        out += c.feed(json.loads(ln))
    out += c.close()
    well_formed(out)
    assert out[-1]["type"] == "text-end" and not c.done


def test_harness_errors_become_error_chunks():
    assert ClaudeConverter().feed({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                   "result": "Not logged in"}) == [{"type": "error", "errorText": "Not logged in"}]
    c = CodexConverter()
    c.feed({"type": "error", "message": "stream disconnected"})
    assert c.feed({"type": "turn.failed", "error": {}}) == [{"type": "error", "errorText": "stream disconnected"}]
    assert c.done


def test_a_tool_result_without_its_call_is_not_sent():
    assert ClaudeConverter().feed({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "nope", "content": "x"}]}}) == []


def test_tool_names_lose_the_harness_prefix():
    assert bare_tool_name("mcp__view__apply_view") == "apply_view"
    assert bare_tool_name("view.undo_view") == "undo_view"
    assert bare_tool_name("mcp__other__x") == "mcp__other__x"


# ---- command lines ----------------------------------------------------------------------------
def turn(tmp_path, resume=None):
    (tmp_path / "cwd").mkdir(exist_ok=True)
    (tmp_path / "scratch").mkdir(exist_ok=True)
    return Turn(prompt="show the orders", mcp_url="http://127.0.0.1:8090/visualizer/mcp/s1", secret="s3cr3t-value",
                instructions="Use the view tools.", resume=resume, workdir=tmp_path / "cwd", scratch=tmp_path / "scratch")


def test_claude_runs_with_the_view_tools_only(tmp_path):
    launch = Claude().launch(turn(tmp_path))
    argv = list(launch.argv)
    assert "s3cr3t-value" not in " ".join(argv) and not launch.env
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--allowedTools") + 1].split(",") == [f"mcp__view__{t}" for t in VIEW_TOOLS]
    for flag in ("-p", "--strict-mcp-config", "--verbose", "--include-partial-messages", "--disable-slash-commands"):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--input-format") + 1] == argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--system-prompt") + 1] == "Use the view tools."
    assert "--resume" not in argv
    config = Path(argv[argv.index("--mcp-config") + 1])
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert json.loads(config.read_text()) == {"mcpServers": {"view": {
        "type": "http", "url": "http://127.0.0.1:8090/visualizer/mcp/s1", "headers": {"Authorization": "Bearer s3cr3t-value"}}}}
    assert json.loads(launch.stdin) == {"type": "user", "message": {"role": "user", "content": "show the orders"}}
    assert launch.close_stdin is False
    (tmp_path / "next").mkdir()
    later = list(Claude().launch(turn(tmp_path / "next", "sess")).argv)
    assert later[later.index("--resume") + 1] == "sess"


def test_codex_runs_read_only_with_the_view_server_only(tmp_path):
    launch = Codex().launch(turn(tmp_path))
    argv = list(launch.argv)
    assert "s3cr3t-value" not in " ".join(argv) and launch.env == {TOKEN_ENV: "s3cr3t-value"}
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    for flag in ("--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only" and argv[argv.index("-C") + 1] == str(tmp_path / "cwd")
    settings = {}
    for i, a in enumerate(argv):
        if a == "-c":
            k, v = argv[i + 1].split("=", 1)
            settings[k] = tomllib.loads(f"v = {v}")["v"]
    assert settings["sandbox_mode"] == "read-only" and settings["approval_policy"] == "never"
    assert settings["mcp_servers.view.bearer_token_env_var"] == TOKEN_ENV
    assert settings["mcp_servers.view.enabled_tools"] == list(VIEW_TOOLS)
    assert settings["developer_instructions"] == "Use the view tools."
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"] == list(DISABLED)
    assert launch.stdin == b"show the orders"
    later = list(Codex().launch(turn(tmp_path, "thread-9")).argv)
    assert later[:4] == ["codex", "exec", "resume", "thread-9"] and "-s" not in later and "-C" not in later


def test_toml_values_survive_any_text():
    text = 'quotes " and \\ and\nnew lines, tabs\t, ünïcode 😀 and \x7f'
    assert tomllib.loads(f"v = {toml_value(text)}")["v"] == text
    assert tomllib.loads(f"v = {toml_value(['a', True, 3])}")["v"] == ["a", True, 3]


def test_the_harness_environment_carries_no_keys(monkeypatch, tmp_path):
    src = {"PATH": "/bin", "HOME": "/home/u", "ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k",
           "OPEN_ONTOLOGIES_TOKEN": "t", "CODEX_HOME": "/home/u/.codex", "HTTPS_PROXY": "http://p:1"}
    own = harness_env(tmp_path, source=src)
    assert own["HOME"] == str(tmp_path) and own["HTTPS_PROXY"] == "http://p:1"
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_ONTOLOGIES_TOKEN", "CODEX_HOME"} & set(own)
    user = harness_env(None, source=src)
    assert user["HOME"] == "/home/u" and user["CODEX_HOME"] == "/home/u/.codex"


def test_status_is_what_each_cli_reports():
    assert Claude().signed_in(0, json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    assert not Claude().signed_in(1, json.dumps({"loggedIn": False}))
    assert not Claude().signed_in(0, "garbage")
    assert Codex().signed_in(0, "Logged in using ChatGPT")
    assert not Codex().signed_in(1, "Not logged in")


def test_login_screens_are_read():
    url = "https://claude.example/oauth/authorize?code=true&state=abc"
    screen = plain("Opening browser to sign in…\r\nIf the browser didn't open, visit: "
                   f"\x1b]8;;{url}\x07\x1b[94m{url}\x1b[39m\x1b]8;;\x07\r\nPaste code here if prompted > ")
    seen = Claude().read_login(screen)
    assert seen.url == url and seen.wants_code and seen.user_code is None
    assert Claude().read_login(screen + "\nLogin failed: That code wasn't accepted.").failure.startswith("Login failed")
    codex = plain("1. Open this link in your browser and sign in to your account\r\n   \x1b[94mhttps://auth.example/codex/device"
                  "\x1b[0m\r\n\r\n2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\r\n   \x1b[94mABCD-12EFG\x1b[0m\r\n")
    seen = Codex().read_login(codex)
    assert (seen.url, seen.user_code, seen.wants_code) == ("https://auth.example/codex/device", "ABCD-12EFG", False)


# ---- sessions, with a fake harness process ---------------------------------------------------------
class FakeMixin:
    """A real adapter whose process is fake_cli.py, replaying a fixture."""

    def setup(self, mode, record):
        self.mode, self.fixtures, self.record = mode, [], record
        return self

    def executable(self, path=None):
        return sys.executable

    def launch(self, t):
        real = super().launch(t)
        fixture = self.fixtures.pop(0) if self.fixtures else FIX / "claude-resume.jsonl"
        argv = ("python", str(FAKE), self.mode, str(fixture), str(self.record), "--", *real.argv)
        return type(real)(argv, env=real.env, stdin=real.stdin, close_stdin=real.close_stdin)

    def version_argv(self):
        return ("python", "-c", "print('9.9.9 (fake)')")

    def status_argv(self):
        return ("python", "-c", "import json; print(json.dumps({'loggedIn': True}))")

    def login_argv(self):
        return ("python", str(HERE / "fake_login.py"), self.name)


class FakeClaude(FakeMixin, Claude):
    pass


class FakeCodex(FakeMixin, Codex):
    pass


class ViewSession:
    def __init__(self, id, owner):
        self.id, self.owner = id, owner
        self.secret = secrets.token_urlsafe(16)
        self.current, self.history, self.events = {}, [], queue.Queue()


class StubEndpoint:
    """The view tools' McpEndpoint, as ONTO-44 provides it: apply_view pushes a data-view event."""

    def __init__(self):
        self.calls = []

    def handle(self, session, headers, body):
        msg = json.loads(body)
        self.calls.append(msg.get("method"))
        if msg.get("method") == "tools/call":
            args = msg["params"]["arguments"]
            session.events.put({"type": "data-view", "id": f"v{len(session.history)}",
                                "data": {"spec": args["spec"], "summary": args["summary"], "undoOf": None}})
            session.history.append(args["spec"])
        return 200, {"Content-Type": "application/json"}, {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"ok": True}}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def service(tmp_path, mode="replay", harness=FakeClaude, **settings):
    h = harness().setup(mode, tmp_path / "record.json")
    return AgentService({h.name: h}, ViewSession, StubEndpoint(), settings=AgentSettings(home=tmp_path / "home", **settings)), h


def chat_body(text="show the orders", view=None):
    return {"id": "c", "trigger": "submit-message", "messageId": None, "view": view,
            "messages": [{"id": "u0", "role": "user", "parts": [{"type": "text", "text": "earlier"}]},
                         {"id": "a0", "role": "assistant", "parts": [{"type": "text", "text": "ok"}]},
                         {"id": "u1", "role": "user", "parts": [{"type": "text", "text": text}]}]}


def events(stream):
    out = [x for x in b"".join(stream).decode().split("\n\n") if x]
    assert all(x.startswith("data: ") for x in out)
    assert out[-1] == "data: [DONE]"
    return [json.loads(x[6:]) for x in out[:-1]]


def test_a_session_runs_turns_and_resumes_the_conversation(tmp_path):
    svc, h = service(tmp_path)
    h.fixtures = [FIX / "claude-turn.jsonl", FIX / "claude-resume.jsonl"]
    sid = svc.create_session("study", "claude")["sessionId"]
    s = svc.sessions[sid]
    view = {"spec": {"id": "v1"}, "compileId": "c1", "picked": ["n1"]}
    chunks = events(svc.chat(sid, chat_body(view=view)))
    assert s.view.current == view
    assert [c["type"] for c in chunks[:2]] == ["start", "start-step"] and chunks[0]["messageId"]
    assert [c["type"] for c in chunks[-2:]] == ["finish-step", "finish"] and chunks[-1]["finishReason"] == "stop"
    well_formed(chunks)
    rec = json.loads((tmp_path / "record.json").read_text())
    assert json.loads(rec["stdin"])["message"]["content"] == "show the orders"
    assert rec["cwd"] == str(s.cwd) and not os.listdir(s.cwd)
    assert "ANTHROPIC_API_KEY" not in rec["env"] and "HOME" in rec["env"]
    assert s.resume == "session-1"
    events(svc.chat(sid, chat_body("and now?")))
    rec = json.loads((tmp_path / "record.json").read_text())
    assert rec["argv"][rec["argv"].index("--resume") + 1] == "session-1"
    assert not list(s.root.glob("turn-*"))                   # each turn's files (the MCP config) are gone
    svc.end_session(sid)
    assert not s.root.exists() and sid not in svc.sessions


def test_codex_turns_resume_their_thread(tmp_path):
    svc, h = service(tmp_path, harness=FakeCodex)
    h.fixtures = [FIX / "codex-turn.jsonl", FIX / "codex-resume.jsonl"]
    sid = svc.create_session("study", "codex")["sessionId"]
    events(svc.chat(sid, chat_body()))
    assert svc.sessions[sid].resume == "thread-1"
    events(svc.chat(sid, chat_body("again")))
    rec = json.loads((tmp_path / "record.json").read_text())
    assert rec["argv"][1:4] == ["exec", "resume", "thread-1"] and rec["stdin"] == "again"
    assert TOKEN_ENV in rec["env"]


def started(svc, sid):
    deadline = time.time() + 10
    while svc.sessions[sid].proc is None and time.time() < deadline:
        time.sleep(0.02)
    return svc.sessions[sid].proc


def test_one_turn_at_a_time_and_stop(tmp_path):
    svc, _ = service(tmp_path, mode="hang", heartbeat=0.2)
    sid = svc.create_session("study", "claude")["sessionId"]
    stream, result = svc.chat(sid, chat_body()), {}
    t = threading.Thread(target=lambda: result.setdefault("body", b"".join(stream)))
    t.start()
    proc = started(svc, sid)
    with pytest.raises(Exception) as busy:
        svc.chat(sid, chat_body())
    assert busy.value.status == 409 and busy.value.code == "turn-in-progress"
    time.sleep(0.5)
    svc.stop(sid)
    t.join(10)
    assert b": ping\n\n" in result["body"]                     # heartbeats while the harness is quiet
    chunks = events([result["body"].replace(b": ping\n\n", b"")])
    assert chunks[-1] == {"type": "abort", "reason": "stopped"}
    assert proc.poll() is not None
    assert not svc.sessions[sid].busy.locked()                # the next turn may start
    svc.end_session(sid)


def test_a_turn_has_a_time_limit(tmp_path):
    svc, _ = service(tmp_path, mode="hang", turn_timeout=0.5)
    sid = svc.create_session("study", "claude")["sessionId"]
    chunks = events(svc.chat(sid, chat_body()))
    assert chunks[-3]["type"] == "error" and "longer than 0.5 s" in chunks[-3]["errorText"]
    assert chunks[-1] == {"type": "finish", "finishReason": "error"}


def test_a_crashed_harness_is_an_error_without_secrets(tmp_path):
    svc, _ = service(tmp_path, mode="crash")
    sid = svc.create_session("study", "claude")["sessionId"]
    chunks = events(svc.chat(sid, chat_body()))
    err = next(c for c in chunks if c["type"] == "error")["errorText"]
    assert "exit 3" in err and "fatal" in err and "abcdefghijklmnop" not in err


def test_a_stream_never_read_frees_its_session(tmp_path):
    svc, _ = service(tmp_path)
    sid = svc.create_session("study", "claude")["sessionId"]
    svc.chat(sid, chat_body()).close()
    assert not svc.sessions[sid].busy.locked() and svc.sessions[sid].proc is None


def test_idle_sessions_expire(tmp_path):
    clock = Clock()
    svc, _ = service(tmp_path, idle_timeout=60)
    svc.clock = clock
    sid = svc.create_session("study", "claude")["sessionId"]
    root = svc.sessions[sid].root
    clock.t += 61
    other = svc.create_session("study", "claude")["sessionId"]
    assert sid not in svc.sessions and other in svc.sessions and not root.exists()


def test_bad_requests(tmp_path):
    svc, _ = service(tmp_path)
    with pytest.raises(Exception) as e:
        svc.create_session("study", "gemini")
    assert e.value.status == 404
    sid = svc.create_session("study", "claude")["sessionId"]
    for body in ({"messages": []}, {"messages": [{"role": "user", "parts": [{"type": "file"}]}]},
                 {"messages": "x"}, {**chat_body(), "view": "spec"}):
        with pytest.raises(Exception) as e:
            svc.chat(sid, body)
        assert e.value.status == 400
    with pytest.raises(Exception) as e:
        svc.chat("nope", chat_body())
    assert e.value.status == 404


def test_harness_list(tmp_path):
    svc, _ = service(tmp_path)
    assert svc.harness_list() == {"harnesses": [{
        "name": "claude", "installed": True, "signedIn": True, "version": "9.9.9",
        "models": [{"id": "default", "label": "Default"}, {"id": "opus", "label": "Opus"},
                   {"id": "sonnet", "label": "Sonnet"}, {"id": "haiku", "label": "Haiku"}],
        "defaultModel": "default", "efforts": ["low", "medium", "high"], "defaultEffort": None}]}


# ---- model and effort per turn (§7, ONTO-47) ----------------------------------------------------
def test_the_choices_come_from_the_config_else_the_built_in_lists():
    from ontologizer.adapters.visualization import harness_claude, harness_codex
    c = Codex().choices()
    assert (list(c.models), c.default_model, c.efforts, c.default_effort) == ([{"id": "default", "label": "Default"}],
                                                                               "default", (), None)
    conf = harness_codex.factory({"models": ["gpt-5.1-codex", {"id": "gpt-5.1", "label": "GPT-5.1"}, "-x",
                                            "a b", {"label": "no id"}, "gpt-5.1"], "model": "o4-mini"}).choices()
    assert list(conf.models) == [{"id": "gpt-5.1-codex", "label": "gpt-5.1-codex"}, {"id": "gpt-5.1", "label": "GPT-5.1"},
                                 {"id": "o4-mini", "label": "o4-mini"}]       # unsafe ids dropped, the default added
    assert conf.default_model == "o4-mini"
    cl = harness_claude.factory({"model": "sonnet", "effort": "high", "efforts": ["low", "high", "max"]}).choices()
    assert cl.default_model == "sonnet" and cl.efforts == ("low", "high", "max") and cl.default_effort == "high"
    assert harness_claude.factory({"model": "--dangerously-skip-permissions"}).choices().default_model == "default"


def test_a_turn_takes_the_model_from_the_turn_else_the_config(tmp_path):
    def flags(h, **kw):
        (tmp_path / "s").mkdir(exist_ok=True)
        for f in (tmp_path / "s").iterdir():
            f.unlink()
        argv = list(h.launch(Turn(prompt="p", mcp_url="u", secret="s", instructions="i", resume="r",
                                  workdir=tmp_path, scratch=tmp_path / "s", **kw)).argv)
        return [(a, argv[i + 1]) for i, a in enumerate(argv) if a in ("--model", "--effort", "-m")]
    assert flags(Claude(model="sonnet", effort="low")) == [("--model", "sonnet"), ("--effort", "low")]
    assert flags(Claude(model="sonnet", effort="low"), model="haiku", effort="high") == [("--model", "haiku"), ("--effort", "high")]
    assert flags(Claude(model="sonnet", effort="low"), model="default", effort="default") == []
    assert flags(Claude(), model="--evil") == []                                 # never an option on the command line
    assert flags(Codex(model="o4-mini")) == [("-m", "o4-mini")]
    assert flags(Codex(model="o4-mini"), model="default") == []
    assert flags(Codex(), model="gpt-5.1", effort="high") == [("-m", "gpt-5.1")]   # Codex takes no effort here


def metadata(chunks):
    return [(i, c["messageMetadata"]) for i, c in enumerate(chunks) if c["type"] == "message-metadata"]


def test_the_model_chosen_on_the_page_applies_to_this_turn_and_the_next(tmp_path):
    svc, h = service(tmp_path)
    h.fixtures = [FIX / "claude-turn.jsonl", FIX / "claude-resume.jsonl", FIX / "claude-resume.jsonl"]
    sid = svc.create_session("study", "claude")["sessionId"]
    chunks = events(svc.chat(sid, {**chat_body(), "agent": {"model": "haiku", "effort": "low"}}))
    well_formed(chunks)
    rec = json.loads((tmp_path / "record.json").read_text())["argv"]
    assert rec[rec.index("--model") + 1] == "haiku" and rec[rec.index("--effort") + 1] == "low"
    # Claude names the answering model in system/init: the chunk follows it, before any text
    assert metadata(chunks) == [(2, {"harness": "claude", "model": "claude-sonnet-4-5-20250929"})]
    assert [c["type"] for c in chunks[:2]] == ["start", "start-step"]
    # the next turn, with no `agent`, keeps the choice and resumes the same conversation
    chunks = events(svc.chat(sid, chat_body("and now?")))
    rec = json.loads((tmp_path / "record.json").read_text())["argv"]
    assert rec[rec.index("--resume") + 1] == "session-1" and rec[rec.index("--model") + 1] == "haiku"
    assert metadata(chunks)[0][1] == {"harness": "claude", "model": "haiku"}      # no init model: the requested one
    # a changed model on a resumed conversation; null effort: Claude's own
    events(svc.chat(sid, {**chat_body("again"), "agent": {"model": "opus", "effort": None}}))
    rec = json.loads((tmp_path / "record.json").read_text())["argv"]
    assert rec[rec.index("--model") + 1] == "opus" and "--effort" not in rec and "--resume" in rec


def test_codex_shows_the_requested_model_right_after_start(tmp_path):
    svc, h = service(tmp_path, harness=FakeCodex)
    h._choices = Codex(models=["gpt-5.1"]).choices()
    h.fixtures = [FIX / "codex-turn.jsonl", FIX / "codex-resume.jsonl"]
    sid = svc.create_session("study", "codex")["sessionId"]
    chunks = events(svc.chat(sid, chat_body()))
    assert metadata(chunks) == [(2, {"harness": "codex", "model": None})]           # default: no model known
    chunks = events(svc.chat(sid, {**chat_body("again"), "agent": {"model": "gpt-5.1", "effort": None}}))
    rec = json.loads((tmp_path / "record.json").read_text())["argv"]
    assert rec[1:4] == ["exec", "resume", "thread-1"] and rec[rec.index("-m") + 1] == "gpt-5.1"
    assert metadata(chunks) == [(2, {"harness": "codex", "model": "gpt-5.1"})]


def test_a_model_or_effort_not_listed_is_refused(tmp_path):
    svc, _ = service(tmp_path)
    sid = svc.create_session("study", "claude")["sessionId"]
    for agent, code in (({"model": "--dangerously-skip-permissions"}, "unknown-model"), ({"model": "gpt-5"}, "unknown-model"),
                        ({"model": 3}, "unknown-model"), ({"effort": "ultra"}, "unknown-effort"), ("haiku", "bad-request")):
        with pytest.raises(Exception) as e:
            svc.chat(sid, {**chat_body(), "agent": agent})
        assert (e.value.status, e.value.code) == (400, code)
    assert not svc.sessions[sid].busy.locked() and svc.sessions[sid].choice == {}


# ---- HTTP: routes, SSE, the MCP endpoint ------------------------------------------------------
@pytest.fixture
def app(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    return Visualizer(Owners(hub), None, Settings(allowed_hosts=(HOST,), mcp_hosts=("127.0.0.1:43210",)))


def call(app, method, path, body=None, headers=None, prefix="/visualizer/api/"):
    h = {"Host": HOST, **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
    r = app.handle(method, prefix + path, h, json.dumps(body).encode() if body is not None else b"")
    return r.status, (json.loads(r.body) if r.body else None)


def test_with_the_agent_off_every_agent_route_is_501(app):
    for method, path, body in (("GET", "agent/harnesses", None), ("POST", "agent/sessions", {"owner": "study"}),
                               ("POST", "agent/harnesses/claude/login", {}), ("DELETE", "agent/sessions/x", None),
                               ("POST", "agent/sessions/x/chat", chat_body())):
        status, out = call(app, method, path, body)
        assert (status, out["error"]["code"]) == (501, "agent-disabled")
    status, out = call(app, "POST", "x", {"jsonrpc": "2.0"}, prefix="/visualizer/mcp/")
    assert (status, out["error"]["code"]) == (501, "agent-disabled")


def test_agent_routes(app, tmp_path):
    app.agent, _ = service(tmp_path)
    assert call(app, "GET", "agent/harnesses")[1]["harnesses"][0]["name"] == "claude"
    assert call(app, "POST", "agent/sessions", {"owner": "ghost", "harness": "claude"})[0] == 404
    status, out = call(app, "POST", "agent/sessions", {"owner": "study", "harness": "claude"})
    sid = out["sessionId"]
    r = app.handle("POST", f"/visualizer/api/agent/sessions/{sid}/chat", {"Host": HOST, "Content-Type": "application/json"},
                   json.dumps(chat_body()).encode())
    assert r.status == 200 and r.body == b"" and dict(r.headers)["Content-Type"] == "text/event-stream"
    events(r.stream)
    assert call(app, "POST", f"agent/sessions/{sid}/stop", {}) == (200, {})
    assert call(app, "GET", f"agent/sessions/{sid}/chat")[0] == 405
    assert call(app, "DELETE", f"agent/sessions/{sid}") == (200, {})
    assert call(app, "DELETE", f"agent/sessions/{sid}")[0] == 404
    assert call(app, "POST", "agent/sessions/x/chat", chat_body(), headers={"Origin": "http://evil.example"})[0] == 403


def test_the_mcp_route_needs_the_bearer_and_the_right_host(app, tmp_path):
    app.agent, _ = service(tmp_path)
    sid = app.agent.create_session("study", "claude")["sessionId"]
    secret = app.agent.sessions[sid].view.secret
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    inner = {"Host": "127.0.0.1:43210"}                       # the service's own port, not an allowed host
    assert call(app, "POST", sid, ping, inner, prefix="/visualizer/mcp/")[0] == 401
    assert call(app, "POST", sid, ping, {**inner, "Authorization": "Bearer wrong"}, prefix="/visualizer/mcp/")[0] == 401
    assert call(app, "POST", "other", ping, {**inner, "Authorization": f"Bearer {secret}"}, prefix="/visualizer/mcp/")[0] == 401
    ok = {**inner, "Authorization": f"Bearer {secret}"}
    assert call(app, "POST", sid, ping, ok, prefix="/visualizer/mcp/") == (200, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert call(app, "POST", sid, ping, {**ok, "Host": "evil.example:43210"}, prefix="/visualizer/mcp/")[0] == 421
    assert call(app, "POST", sid, ping, {**ok, "Origin": "http://127.0.0.1:43210"}, prefix="/visualizer/mcp/")[0] == 403
    assert call(app, "GET", sid, None, ok, prefix="/visualizer/mcp/")[0] == 405
    assert call(app, "POST", f"{sid}/x", ping, ok, prefix="/visualizer/mcp/")[0] == 404
    # the page's own host is no way into the API's MCP twin, nor the inner host into the API
    assert call(app, "GET", "agent/harnesses", None, inner)[0] == 421


@pytest.fixture
def live(tmp_path, monkeypatch):
    """The real HTTP server (server.build) with the agent on, its harness the fake Claude."""
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    data = yaml.safe_load((hub / "ontologizer.yaml").read_text())
    data["visualizer"] = {"agent": {"enabled": True}}
    (hub / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    svc, h = service(tmp_path, mode="live", heartbeat=0.2)
    monkeypatch.setattr(server, "build_agent", lambda conf, hub_, app: svc)
    srv = server.build(hub, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, svc, h
    srv.shutdown()
    srv.server_close()


def http(port, method, path, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"http://127.0.0.1:{port}/visualizer/api/{path}", None if body is None else json.dumps(body).encode(),
                                 {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}, method=method)
    return opener.open(req, timeout=30)


def test_the_chat_streams_server_sent_events_with_the_view_interleaved(live):
    srv, svc, h = live
    port = srv.server_address[1]
    assert svc.mcp_origin == f"http://127.0.0.1:{port}"
    h.fixtures = [FIX / "claude-turn.jsonl"]
    sid = json.loads(http(port, "POST", "agent/sessions", {"owner": "study", "harness": "claude"}).read())["sessionId"]
    r = http(port, "POST", f"agent/sessions/{sid}/chat", chat_body())
    assert r.headers["Content-Type"] == "text/event-stream" and r.headers["Cache-Control"] == "no-cache"
    assert r.headers["x-vercel-ai-ui-message-stream"] == "v1" and r.headers.get("Content-Length") is None
    chunks = events([r.read()])
    types = [c["type"] for c in chunks]
    i = types.index("data-view")
    assert types[i - 1] == "tool-output-available" and chunks[i - 1]["output"]["ok"] is True
    assert chunks[i]["data"]["summary"] == "only Order" and types.count("data-view") == 1
    assert svc.mcp_endpoint.calls == ["tools/call"]           # the fake harness reached the MCP route
    assert http(port, "DELETE", f"agent/sessions/{sid}").status == 200


# ---- sign-in -----------------------------------------------------------------------------------
def wait_for(svc, lid, *states, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = svc.logins.status(lid)
        if st["state"] in states:
            return st
        time.sleep(0.05)
    raise AssertionError(f"still {svc.logins.status(lid)}")


def test_claude_sign_in_takes_the_pasted_code(tmp_path):
    svc, _ = service(tmp_path)
    lid = svc.logins.start("claude")["loginId"]
    st = wait_for(svc, lid, "enter-code")
    assert st["url"] == "https://claude.example/oauth/authorize?code=true&client_id=x&state=abc" and st["userCode"] is None
    with pytest.raises(Exception) as e:
        svc.logins.code(lid, "has space")
    assert e.value.status == 400
    assert svc.logins.code(lid, "good#state") == {"state": "starting"}
    assert wait_for(svc, lid, "done", "failed") == {"state": "done", "url": st["url"], "userCode": None, "message": None}
    assert (tmp_path / "home").is_dir()


def test_claude_sign_in_with_a_wrong_code_fails_with_its_message(tmp_path):
    svc, _ = service(tmp_path)
    lid = svc.logins.start("claude")["loginId"]
    wait_for(svc, lid, "enter-code")
    svc.logins.code(lid, "bad#code")
    st = wait_for(svc, lid, "failed")
    assert st["message"].startswith("Login failed: That code wasn't accepted")
    with pytest.raises(Exception) as e:
        svc.logins.code(lid, "again")
    assert e.value.status == 409


def test_codex_sign_in_shows_the_device_code(tmp_path):
    svc, _ = service(tmp_path, harness=FakeCodex)
    lid = svc.logins.start("codex")["loginId"]
    st = wait_for(svc, lid, "show-code")
    assert (st["url"], st["userCode"]) == ("https://auth.example/codex/device", "ABCD-12EFG")
    with pytest.raises(Exception) as e:
        svc.logins.code(lid, "x")
    assert e.value.status == 409
    assert wait_for(svc, lid, "done")["state"] == "done"


def test_sign_in_cancel_and_timeout(tmp_path):
    svc, h = service(tmp_path)
    h.login_argv = lambda: ("python", str(HERE / "fake_login.py"), "slow")
    lid = svc.logins.start("claude")["loginId"]
    login = svc.logins.logins[lid]
    assert svc.logins.cancel(lid) == {}
    assert wait_for(svc, lid, "failed")["message"] == "cancelled"
    assert login.proc.poll() is not None
    svc.settings.login_timeout = 0.5
    lid = svc.logins.start("claude")["loginId"]
    assert wait_for(svc, lid, "failed")["message"] == "the sign-in timed out"
    assert svc.logins.status(lid)["url"] is None
    with pytest.raises(Exception) as e:
        svc.logins.status("nope")
    assert e.value.status == 404


def test_a_new_sign_in_cancels_the_running_one(tmp_path):
    svc, h = service(tmp_path)
    h.login_argv = lambda: ("python", str(HERE / "fake_login.py"), "slow")
    first = svc.logins.start("claude")["loginId"]
    svc.logins.start("claude")
    assert wait_for(svc, first, "failed")["message"] == "cancelled"


def test_a_client_that_goes_away_ends_the_turn(live):
    srv, svc, h = live
    port = srv.server_address[1]
    h.mode = "hang"
    sid = json.loads(http(port, "POST", "agent/sessions", {"owner": "study", "harness": "claude"}).read())["sessionId"]
    conn = http_client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", f"/visualizer/api/agent/sessions/{sid}/chat", json.dumps(chat_body()),
                 {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 200
    proc = started(svc, sid)
    resp.close()
    conn.close()
    assert proc.wait(10) is not None
    deadline = time.time() + 5
    while svc.sessions[sid].busy.locked() and time.time() < deadline:
        time.sleep(0.05)
    assert not svc.sessions[sid].busy.locked()


def test_the_agent_setting_reaches_the_container_without_a_home_of_this_machine(tmp_path):
    from ontologizer.adapters.visualization.deploy import generate
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    (hub / "ontologizer.local.yaml").write_text(yaml.safe_dump({"projects": {"study": "../study"}, "visualizer": {
        "agent": {"enabled": True, "home": "/Users/me", "claude": {"model": "sonnet"}}}}))
    generate(hub, tmp_path / "out", "http://engine:8080")
    reg = yaml.safe_load((tmp_path / "out" / "ontologizer.local.yaml").read_text())
    assert reg["visualizer"] == {"agent": {"enabled": True, "claude": {"model": "sonnet"}}}


def test_the_service_reads_the_agent_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUALIZER_AGENT_HOME", str(tmp_path / "vol"))
    s = server.agent_settings({"home": "/elsewhere", "turn_timeout": 60, "max_sessions": "3"})
    assert (s.home, s.turn_timeout, s.max_sessions) == (tmp_path / "vol", 60.0, 3)
    hub = make_hub(tmp_path, {"study": "../study"})
    srv = server.build(hub, "127.0.0.1", 0)                  # not enabled: no agent
    assert srv.app.agent is None
    srv.server_close()


def test_the_service_builds_view_sessions_with_the_owner_and_id_in_their_places():
    """The bridge calls new_view_session(session_id, owner); the view tools' ViewSession takes owner first."""
    from pathlib import Path
    from ontologizer.adapters.visualization import server
    svc = server.build_agent({"harnesses": ["claude"]}, Path("."), app=object())
    vs = svc.new_view_session("sid-1", "owner-1")
    assert (vs.id, vs.owner) == ("sid-1", "owner-1") and vs.secret
