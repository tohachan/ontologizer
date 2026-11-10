"""The visualizer as a local HTTP service (standard library only).

    ontologizer visualizer [--hub DIR] [--host 127.0.0.1] [--port 8090] [--allow-host HOST:PORT …]

The hub is `--hub`, else `$ONTOLOGIZER_HUB`, else this kit's checkout; the store address is the
hub's `store.url` (its `ontologizer.local.yaml` may override it). The service answers only to the
Host names it serves under: by default `127.0.0.1:PORT` and `localhost:PORT`. Behind a published
container port, name the host's address with `--allow-host` or `$VISUALIZER_ALLOWED_HOSTS`
(comma-separated).

The agent (docs/visualizer-agent.md) is off unless the hub sets `visualizer.agent.enabled: true`. Its
harnesses reach the view tools at `http://127.0.0.1:<this port>/visualizer/mcp/<session>`, so that Host
is always accepted on that route; behind a published port, the port inside the container is meant.
"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ontologizer.adapters.engine.open_ontologies import OpenOntologies
from ontologizer.adapters.visualization.agent import DEFAULT_INSTRUCTIONS, AgentService, AgentSettings
from ontologizer.adapters.visualization.app import Settings, Visualizer, error_response
from ontologizer.core import plugins
from ontologizer.core.config import read_hub
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import ScopedReader

KIT_ROOT = Path(__file__).resolve().parents[4]


def default_hub() -> Path:
    return Path(os.environ.get("ONTOLOGIZER_HUB") or KIT_ROOT).resolve()


def agent_settings(conf: dict) -> AgentSettings:
    home = os.environ.get("VISUALIZER_AGENT_HOME") or conf.get("home")
    s = AgentSettings(home=Path(home).expanduser() if home else None)
    for key in ("turn_timeout", "idle_timeout", "max_sessions", "login_timeout"):
        if conf.get(key) is not None:
            setattr(s, key, type(getattr(s, key))(conf[key]))
    return s


def build_agent(conf: dict, hub: Path, app: Visualizer):
    """The agent bridge, when the hub enables it. Its harnesses are adapters chosen by name
    (`visualizer.agent.harnesses`, default claude and codex; options under `visualizer.agent.<name>`)."""
    from ontologizer.adapters.visualization import view_tools                 # ONTO-44
    harnesses = {}
    for name in conf.get("harnesses") or ("claude", "codex"):
        try:
            harnesses[name] = plugins.load("view_agent", {"adapter": name, **(conf.get(name) or {})}, base=hub)
        except Exception as e:
            print(f"visualizer: agent harness {name!r} not loaded ({type(e).__name__}: {e})", file=sys.stderr)
    instructions_file = Path(view_tools.__file__).with_name("agent_instructions.md")
    instructions = instructions_file.read_text() if instructions_file.is_file() else DEFAULT_INSTRUCTIONS
    tools = view_tools.ViewTools(app)
    new_view_session = lambda sid, owner: view_tools.ViewSession(owner=owner, id=sid)   # noqa: E731 — fields by name
    return AgentService(harnesses, new_view_session, view_tools.McpEndpoint(tools),
                        instructions=instructions, settings=agent_settings(conf))


def build(hub: Path, host: str, port: int, allow_hosts=()) -> ThreadingHTTPServer:
    hub_data = read_hub(hub)
    url = (hub_data.get("store") or {}).get("url")
    planner = None
    conf = (hub_data.get("visualizer") or {}).get("planner")
    if conf:
        try:
            planner = plugins.load("view_planner", dict(conf), base=hub)
        except Exception as e:                     # the service runs without it rather than not at all
            print(f"visualizer: planner {conf.get('adapter')!r} not loaded ({type(e).__name__}: {e}); it is off",
                  file=sys.stderr)
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}", *allow_hosts)
    static = Path(os.environ.get("VISUALIZER_STATIC") or KIT_ROOT / "web" / "visualizer" / "dist")
    app = Visualizer(Owners(hub), ScopedReader(OpenOntologies(url)),
                     Settings(allowed_hosts=tuple(dict.fromkeys(allowed)), static_dir=static), planner=planner)
    agent_conf = (hub_data.get("visualizer") or {}).get("agent") or {}
    if agent_conf.get("enabled") is True:
        try:
            app.agent = build_agent(agent_conf, hub, app)
        except Exception as e:                     # as with the planner: the service runs without it
            print(f"visualizer: agent not started ({type(e).__name__}: {e}); it is off", file=sys.stderr)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ontologizer-visualizer"
        sys_version = ""

        def _serve(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > app.settings.max_body:
                resp = error_response(413, "payload-too-large", "the request body is missing a length or too large")
                self.close_connection = True                    # the body is never read
            else:
                resp = app.handle(self.command, self.path, dict(self.headers), self.rfile.read(length))
            if resp.stream is not None:
                self._stream(resp)
                return
            self.send_response(resp.status)
            for k, v in resp.headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            self.wfile.write(resp.body)

        def _stream(self, resp):
            """Server-sent events: no length, the connection closes at the end (HTTP/1.0), each event is
            flushed as it comes. A client that goes away ends the stream, and with it the harness's turn."""
            self.close_connection = True
            try:
                self.send_response(resp.status)
                for k, v in resp.headers:
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
                for data in resp.stream:
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                close = getattr(resp.stream, "close", None)
                if close:
                    close()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

        def log_message(self, fmt, *args):
            sys.stderr.write(f"visualizer: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    real = server.server_address[1]
    if port == 0:                                  # a port chosen by the system (tests)
        app.settings.allowed_hosts = tuple(dict.fromkeys((f"127.0.0.1:{real}", f"localhost:{real}", *allow_hosts)))
    app.settings.mcp_hosts = (f"127.0.0.1:{real}",)
    if app.agent is not None:
        app.agent.mcp_origin = f"http://127.0.0.1:{real}"
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ontologizer visualizer", description=__doc__.split("\n\n")[0])
    ap.add_argument("--hub", type=Path, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--allow-host", action="append", default=[])
    a = ap.parse_args(argv)
    extra = [h for h in os.environ.get("VISUALIZER_ALLOWED_HOSTS", "").split(",") if h.strip()]
    server = build((a.hub or default_hub()).resolve(), a.host, a.port, [*a.allow_host, *map(str.strip, extra)])
    print(f"visualizer: http://127.0.0.1:{a.port}/ (answers to {', '.join(server.app.settings.allowed_hosts)})",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
