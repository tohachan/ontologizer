"""The visualizer's agent bridge (docs/visualizer-agent.md §1–§3, §5, §6): chat sessions, the harness
processes, sign-in, and the chat stream to the page.

- A session belongs to one browser tab. It holds the view session the MCP endpoint serves (its secret,
  current view and pushed events), the harness's conversation id, and an empty private working directory.
- A turn is one harness process. A session runs one turn at a time; a turn has a time limit and can be
  stopped. Its JSON events become AI SDK UI message chunks, sent as server-sent events.
- A sign-in runs the harness's login command in a pseudo-terminal and exposes the URL and code it shows.

Harness processes get a reduced environment: no API keys, no variables of the service. Their HOME is the
agent's state directory: `$VISUALIZER_AGENT_HOME` or the hub's `visualizer.agent.home`, else the user's own.
"""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import pty
import queue
import re
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ontologizer.adapters.visualization.app import ApiError
from ontologizer.adapters.visualization.harness_base import plain
from ontologizer.ports.view_agent import Harness, Turn

SSE_HEADERS = (("Content-Type", "text/event-stream"), ("Cache-Control", "no-cache"),
               ("x-vercel-ai-ui-message-stream", "v1"), ("X-Content-Type-Options", "nosniff"),
               ("X-Accel-Buffering", "no"))
DEFAULT_INSTRUCTIONS = ("You change the diagram open in the ontologizer visualizer on the user's request. You can "
                        "only use the view tools: read the current view, search and describe concepts, preview and "
                        "apply a view, undo. Answer briefly, in the user's language.")
# Variables a harness may need from the service's environment; everything else stays out (API keys too:
# the harnesses use the subscription they are signed in with).
_PASS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME",
         "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
         "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "NODE_USE_ENV_PROXY", "REQUESTS_CA_BUNDLE")
# With the user's own HOME, the CLIs' own location overrides apply as in the user's shell.
_PASS_USER_HOME = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=.]{24,}")
MAX_PROMPT = 8000
MAX_LINE = 8 << 20


@dataclass
class AgentSettings:
    turn_timeout: float = 180
    idle_timeout: float = 1800
    max_sessions: int = 8
    login_timeout: float = 900
    status_ttl: float = 15
    heartbeat: float = 5                     # an SSE comment after this long without a chunk: finds a closed tab
    home: Path | None = None                 # the harnesses' HOME; None: the user's own


def harness_env(home: Path | None, extra: Mapping[str, str] = (), source: Mapping[str, str] | None = None) -> dict:
    src = os.environ if source is None else source
    names = _PASS + (() if home is not None else _PASS_USER_HOME)
    env = {k: src[k] for k in names if k in src}
    env.setdefault("PATH", os.defpath)
    env["HOME"] = str(home) if home is not None else src.get("HOME", str(Path.home()))
    env["DISABLE_AUTOUPDATER"] = "1"
    env.update(extra)
    return env


def _redact(text: str) -> str:
    return _TOKENISH.sub("…", text)


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)          # the harness and anything it started
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def sse(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode() + b"\n\n"


class TurnStream:
    """The body of a chat response: SSE bytes. Closing it (or the client going away) ends the turn and
    frees the session for the next one."""

    headers = SSE_HEADERS

    def __init__(self, chunks: Iterator[dict | None], release: Callable[[], None]):
        self._chunks, self._release, self._closed = chunks, release, False

    def __iter__(self) -> Iterator[bytes]:
        try:
            for c in self._chunks:
                yield b": ping\n\n" if c is None else sse(c)
            yield b"data: [DONE]\n\n"
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._chunks.close()
            finally:
                self._release()


@dataclass
class AgentSession:
    id: str
    owner: str | None                        # unused since ONTO-49: the chat request's `view.owner` names it
    harness: str
    view: object                             # the view tools' ViewSession: .secret, .current, .events
    root: Path                               # private: cwd/ (the harness's empty working directory), turn files
    last_used: float
    resume: str | None = None
    busy: threading.Lock = field(default_factory=threading.Lock)
    proc: subprocess.Popen | None = None
    stopping: threading.Event = field(default_factory=threading.Event)
    turns: int = 0

    @property
    def cwd(self) -> Path:
        return self.root / "cwd"


class AgentService:
    def __init__(self, harnesses: Mapping[str, Harness], new_view_session: Callable[[str, str], object],
                 mcp_endpoint, *, instructions: str = DEFAULT_INSTRUCTIONS, settings: AgentSettings | None = None,
                 mcp_origin: str = "http://127.0.0.1:8090", clock: Callable[[], float] = time.monotonic):
        self.harnesses = dict(harnesses)
        self.new_view_session, self.mcp_endpoint = new_view_session, mcp_endpoint
        self.instructions = instructions
        self.settings = settings or AgentSettings()
        self.mcp_origin = mcp_origin             # where the harness reaches this service: 127.0.0.1:<own port>
        self.clock = clock
        self.sessions: dict[str, AgentSession] = {}
        self.lock = threading.Lock()
        self.logins = LoginManager(self)
        self._status: dict[str, tuple[float, bool]] = {}
        self._versions: dict[str, str | None] = {}

    # ---- environment and harnesses ----------------------------------------------------
    def env(self, extra: Mapping[str, str] = ()) -> dict:
        return harness_env(self.settings.home, extra)

    def harness(self, name) -> Harness:
        h = self.harnesses.get(name) if isinstance(name, str) else None
        if h is None:
            raise ApiError(404, "unknown-harness", f"no harness {name!r}; one of {sorted(self.harnesses)}")
        return h

    def _exe(self, h: Harness) -> str | None:
        return h.executable(self.env().get("PATH"))

    def _run(self, h: Harness, argv, timeout=20) -> tuple[int, str]:
        exe = self._exe(h)
        if exe is None:
            return 127, ""
        try:
            p = subprocess.run([exe, *argv[1:]], env=self.env(), capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""
        return p.returncode, p.stdout + p.stderr

    def signed_in(self, name: str, fresh: bool = False) -> bool:
        h = self.harness(name)
        hit = self._status.get(name)
        if not fresh and hit and hit[0] > self.clock():
            return hit[1]
        rc, out = self._run(h, h.status_argv())
        ok = h.signed_in(rc, out)
        self._status[name] = (self.clock() + self.settings.status_ttl, ok)
        return ok

    def forget_status(self, name: str) -> None:
        self._status.pop(name, None)

    def version(self, name: str) -> str | None:
        if name not in self._versions:
            h = self.harness(name)
            rc, out = self._run(h, h.version_argv())
            m = re.search(r"\d+\.\d+\.\d+\S*", out) if rc == 0 else None
            self._versions[name] = m.group(0) if m else None
        return self._versions[name]

    def harness_list(self) -> dict:
        out = []
        for name, h in self.harnesses.items():
            installed = self._exe(h) is not None
            out.append({"name": name, "installed": installed,
                        "signedIn": installed and self.signed_in(name),
                        "version": self.version(name) if installed else None})
        return {"harnesses": out}

    # ---- sessions -------------------------------------------------------------------
    def _sweep(self) -> None:
        now = self.clock()
        with self.lock:
            stale = [s for s in self.sessions.values()
                     if now - s.last_used > self.settings.idle_timeout and not s.busy.locked()]
        for s in stale:
            self.end_session(s.id)

    def session(self, sid) -> AgentSession:
        s = self.sessions.get(sid) if isinstance(sid, str) else None
        if s is None:
            raise ApiError(404, "unknown-session", "no such agent session; start a new one")
        return s

    def create_session(self, owner: str | None, harness) -> dict:
        self._sweep()
        h = self.harness(harness)
        if self._exe(h) is None:
            raise ApiError(409, "harness-not-installed", f"{harness} is not installed where the service runs")
        with self.lock:
            if len(self.sessions) >= self.settings.max_sessions:
                idle = sorted((s for s in self.sessions.values() if not s.busy.locked()), key=lambda s: s.last_used)
                if not idle:
                    raise ApiError(503, "busy", "too many agent sessions are running; retry later")
                victim = idle[0].id
            else:
                victim = None
        if victim:
            self.end_session(victim)
        sid = uuid.uuid4().hex
        root = Path(tempfile.mkdtemp(prefix="viz-agent-"))
        (root / "cwd").mkdir(mode=0o700)
        s = AgentSession(sid, owner, h.name, self.new_view_session(sid, owner), root, self.clock())
        with self.lock:
            self.sessions[sid] = s
        return {"sessionId": sid}

    def end_session(self, sid) -> dict:
        with self.lock:
            s = self.sessions.pop(sid, None) if isinstance(sid, str) else None
        if s is None:
            raise ApiError(404, "unknown-session", "no such agent session")
        s.stopping.set()
        _kill(s.proc)
        shutil.rmtree(s.root, ignore_errors=True)
        return {}

    def stop(self, sid) -> dict:
        s = self.session(sid)
        if s.busy.locked():
            s.stopping.set()
            _kill(s.proc)
        return {}

    # ---- MCP --------------------------------------------------------------------------
    def mcp(self, sid: str, headers: Mapping[str, str], body: bytes):
        """`POST /visualizer/mcp/{sid}`: the bearer check, then the view tools' endpoint."""
        s = self.sessions.get(sid)
        h = {k.lower(): v for k, v in headers.items()}
        auth = h.get("authorization", "")
        secret = getattr(s.view, "secret", None) if s else None
        if s is None or not secret or not hmac.compare_digest(auth.encode(), f"Bearer {secret}".encode()):
            raise ApiError(401, "unauthorized", "this endpoint needs the session's bearer secret")
        s.last_used = self.clock()
        return self.mcp_endpoint.handle(s.view, headers, body)

    # ---- chat -------------------------------------------------------------------------
    @staticmethod
    def prompt_of(payload: dict) -> str:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ApiError(400, "bad-request", "messages must be a list of UI messages")
        last = next((m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None)
        parts = last.get("parts") if last else None
        text = "\n".join(p["text"] for p in parts or [] if isinstance(p, dict) and p.get("type") == "text"
                         and isinstance(p.get("text"), str)).strip()
        if not text:
            raise ApiError(400, "bad-request", "the last user message has no text")
        if len(text) > MAX_PROMPT:
            raise ApiError(413, "payload-too-large", f"a message is at most {MAX_PROMPT} characters")
        return text

    def chat(self, sid, payload: dict) -> TurnStream:
        self._sweep()
        s = self.session(sid)
        prompt = self.prompt_of(payload)
        view = payload.get("view")
        if view is not None and not isinstance(view, dict):
            raise ApiError(400, "bad-request", "view must be an object")
        if not s.busy.acquire(blocking=False):
            raise ApiError(409, "turn-in-progress", "this session is still answering; stop it or wait")
        try:
            if view is not None:
                setter = getattr(s.view, "set_current", None)          # the view tools normalise it (ONTO-44)
                if setter: setter(view)
                else: s.view.current = view
            s.stopping.clear()
            s.last_used = self.clock()
        except BaseException:
            s.busy.release()
            raise

        def release():
            s.last_used = self.clock()
            s.proc = None
            s.busy.release()
        return TurnStream(self._turn(s, prompt), release)

    def _drain(self, s: AgentSession) -> list[dict]:
        out = []
        events = getattr(s.view, "events", None)
        while events is not None:
            try:
                out.append(events.get_nowait())
            except queue.Empty:
                break
        return out

    def _turn(self, s: AgentSession, prompt: str) -> Iterator[dict | None]:
        """The turn's chunks; None now and then while the harness is quiet (a heartbeat)."""
        h = self.harnesses[s.harness]
        yield {"type": "start", "messageId": f"msg-{uuid.uuid4().hex}"}
        yield {"type": "start-step"}
        s.turns += 1
        scratch = s.root / f"turn-{s.turns}"
        scratch.mkdir(mode=0o700)
        proc = None
        try:
            exe = self._exe(h)
            if exe is None:
                yield {"type": "error", "errorText": f"{s.harness} is not installed where the service runs"}
                yield {"type": "finish-step"}
                yield {"type": "finish", "finishReason": "error"}
                return
            launch = h.launch(Turn(prompt=prompt, mcp_url=f"{self.mcp_origin}/visualizer/mcp/{s.id}",
                                   secret=s.view.secret, instructions=self.instructions, resume=s.resume,
                                   workdir=s.cwd, scratch=scratch))
            conv = h.converter()
            proc = subprocess.Popen([exe, *launch.argv[1:]], cwd=s.cwd, env=self.env(launch.env),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True)
            s.proc = proc
            if s.stopping.is_set():                      # stopped while it was starting
                _kill(proc)
            lines: queue.Queue = queue.Queue()
            tail = bytearray()

            def read_out():
                for line in iter(lambda: proc.stdout.readline(MAX_LINE), b""):
                    lines.put(line)
                lines.put(None)

            def read_err():
                for chunk in iter(lambda: proc.stderr.read1(4096), b""):
                    tail.extend(chunk)
                    del tail[:-4096]
            threading.Thread(target=read_out, daemon=True).start()
            threading.Thread(target=read_err, daemon=True).start()
            try:
                proc.stdin.write(launch.stdin)
                proc.stdin.flush()
                if launch.close_stdin:
                    proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            deadline = self.clock() + self.settings.turn_timeout
            timed_out = False
            quiet = time.monotonic()
            while True:
                if self.clock() > deadline:
                    timed_out = True
                    _kill(proc)
                    break
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    if time.monotonic() - quiet > self.settings.heartbeat:
                        quiet = time.monotonic()
                        yield None
                    continue
                quiet = time.monotonic()
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                for chunk in conv.feed(event):
                    yield chunk
                    if chunk["type"] in ("tool-output-available", "tool-output-error"):
                        yield from self._drain(s)
                if conv.done and not launch.close_stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill(proc)
                proc.wait()
            if conv.resume_id and (conv.done or s.resume is None):
                s.resume = conv.resume_id
            yield from conv.close()
            yield from self._drain(s)
            if s.stopping.is_set():
                yield {"type": "abort", "reason": "stopped"}
                return
            failed = False
            if timed_out:
                failed = True
                yield {"type": "error", "errorText": f"the turn took longer than {self.settings.turn_timeout:g} s "
                                                     "and was stopped"}
            elif not conv.done:
                failed = True
                last = next((ln for ln in reversed(bytes(tail).decode(errors="replace").splitlines()) if ln.strip()), "")
                yield {"type": "error", "errorText": f"{s.harness} ended without finishing the turn "
                                                     f"(exit {proc.returncode})" + (f": {_redact(last)[:300]}" if last else "")}
            yield {"type": "finish-step"}
            yield {"type": "finish", "finishReason": "error" if failed else "stop"}
        finally:
            _kill(proc)
            if proc is not None:
                for f in (proc.stdin, proc.stdout, proc.stderr):
                    try:
                        f.close()
                    except OSError:
                        pass
            shutil.rmtree(scratch, ignore_errors=True)


# ---- sign-in ----------------------------------------------------------------------------------
@dataclass
class Login:
    id: str
    harness: str
    started: float
    state: str = "starting"
    url: str | None = None
    user_code: str | None = None
    message: str | None = None
    code_sent: bool = False
    finished: float | None = None
    proc: subprocess.Popen | None = None
    master: int | None = None
    cancelled: bool = False
    failure: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict:
        return {"state": self.state, "url": self.url, "userCode": self.user_code, "message": self.message}


class LoginManager:
    """Runs a harness's sign-in command in a pseudo-terminal (docs/visualizer-agent.md §5). The terminal
    is wide, so that the URL is never wrapped. Only the URL, the device code and the command's own
    failure line leave it; the output is not kept once the sign-in ends."""

    COLUMNS = 1000
    KEEP = 600                                   # a finished sign-in is answered for this long

    def __init__(self, service: AgentService):
        self.service = service
        self.logins: dict[str, Login] = {}
        self.lock = threading.Lock()

    def _sweep(self):
        now = self.service.clock()
        with self.lock:
            for k in [k for k, v in self.logins.items() if v.finished and now - v.finished > self.KEEP]:
                del self.logins[k]

    def get_login(self, lid) -> Login:
        self._sweep()
        login = self.logins.get(lid) if isinstance(lid, str) else None
        if login is None:
            raise ApiError(404, "unknown-login", "no such sign-in")
        return login

    def start(self, name) -> dict:
        self._sweep()
        h = self.service.harness(name)
        exe = self.service._exe(h)
        if exe is None:
            raise ApiError(409, "harness-not-installed", f"{name} is not installed where the service runs")
        for other in [v for v in self.logins.values() if v.harness == h.name and not v.finished]:
            self.cancel(other.id)
        home = self.service.settings.home
        if home is not None:
            home.mkdir(parents=True, exist_ok=True)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, self.COLUMNS, 0, 0))
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO                    # a pasted code is never echoed into the output
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        try:
            proc = subprocess.Popen([exe, *h.login_argv()[1:]], stdin=slave, stdout=slave, stderr=slave,
                                    env=self.service.env({"TERM": "xterm-256color", "COLUMNS": str(self.COLUMNS)}),
                                    cwd=home or tempfile.gettempdir(), start_new_session=True, close_fds=True)
        except OSError:
            os.close(master)
            raise ApiError(500, "login-failed", f"the {name} sign-in command could not start") from None
        finally:
            os.close(slave)
        login = Login(uuid.uuid4().hex, h.name, self.service.clock(), proc=proc, master=master)
        with self.lock:
            self.logins[login.id] = login
        threading.Thread(target=self._watch, args=(login, h), daemon=True).start()
        return {"loginId": login.id}

    def _watch(self, login: Login, h: Harness) -> None:
        buf = ""
        deadline = login.started + self.service.settings.login_timeout
        timed_out = False
        try:
            while True:
                if self.service.clock() > deadline:
                    timed_out = True
                    _kill(login.proc)
                    break
                try:
                    ready, _, _ = select.select([login.master], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if not ready:
                    if login.proc.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(login.master, 4096)
                except OSError:                  # EIO: the command closed its terminal
                    break
                if not data:
                    break
                buf = (buf + data.decode(errors="replace"))[-65536:]
                self._update(login, h, plain(buf))
            try:
                rc = login.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill(login.proc)
                rc = login.proc.wait()
            self._update(login, h, plain(buf))
            with login.lock:
                if login.cancelled:
                    login.state, login.message = "failed", "cancelled"
                elif timed_out:
                    login.state, login.message = "failed", "the sign-in timed out"
                elif rc == 0:
                    login.state, login.message = "done", None
                else:
                    login.state = "failed"
                    login.message = login.failure or f"the sign-in command ended (exit {rc})"
                login.user_code = None if login.state == "done" else login.user_code
        finally:
            buf = ""
            try:
                os.close(login.master)
            except OSError:
                pass
            login.finished = self.service.clock()
            self.service.forget_status(login.harness)

    @staticmethod
    def _update(login: Login, h: Harness, screen: str) -> None:
        seen = h.read_login(screen)
        with login.lock:
            if seen.failure:
                login.failure = _redact(seen.failure)[:300]
            if login.finished or login.state in ("done", "failed"):
                return
            if seen.url and seen.url.startswith("https://"):
                login.url = seen.url
            if seen.user_code:
                login.user_code = seen.user_code
            if login.code_sent:
                return
            if login.url and login.user_code:
                login.state = "show-code"
            elif login.url and seen.wants_code:
                login.state = "enter-code"
            elif login.url:
                login.state = "open-url"

    def status(self, lid) -> dict:
        return self.get_login(lid).public()

    def code(self, lid, code) -> dict:
        login = self.get_login(lid)
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 4096 or re.search(r"[\x00-\x1f\x7f\s]", code.strip()):
            raise ApiError(400, "bad-request", "code is the text the sign-in page shows, without spaces")
        with login.lock:
            if login.state != "enter-code":
                raise ApiError(409, "login-not-waiting", "this sign-in does not wait for a code")
            try:
                os.write(login.master, code.strip().encode() + b"\r")
            except OSError:
                raise ApiError(409, "login-not-waiting", "the sign-in command has ended") from None
            login.code_sent = True
            login.state, login.message = "starting", "checking the code"
        return {"state": login.state}

    def cancel(self, lid) -> dict:
        login = self.get_login(lid)
        with login.lock:
            login.cancelled = True
        _kill(login.proc)
        return {}
