"""The visualizer as a local HTTP service (standard library only).

    ontologizer visualizer [--hub DIR] [--host 127.0.0.1] [--port 8090] [--allow-host HOST:PORT …]

The hub is `--hub`, else `$ONTOLOGIZER_HUB`, else this kit's checkout; the store address is the
hub's `store.url` (its `ontologizer.local.yaml` may override it). The service answers only to the
Host names it serves under: by default `127.0.0.1:PORT` and `localhost:PORT`. Behind a published
container port, name the host's address with `--allow-host` or `$VISUALIZER_ALLOWED_HOSTS`
(comma-separated).
"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ontologizer.adapters.engine.open_ontologies import OpenOntologies
from ontologizer.adapters.visualization.app import Settings, Visualizer, error_response
from ontologizer.core.config import read_hub
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import ScopedReader

KIT_ROOT = Path(__file__).resolve().parents[4]


def default_hub() -> Path:
    return Path(os.environ.get("ONTOLOGIZER_HUB") or KIT_ROOT).resolve()


def build(hub: Path, host: str, port: int, allow_hosts=()) -> ThreadingHTTPServer:
    url = (read_hub(hub).get("store") or {}).get("url")
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}", *allow_hosts)
    app = Visualizer(Owners(hub), ScopedReader(OpenOntologies(url)), Settings(allowed_hosts=tuple(dict.fromkeys(allowed))))

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
            self.send_response(resp.status)
            for k, v in resp.headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            self.wfile.write(resp.body)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

        def log_message(self, fmt, *args):
            sys.stderr.write(f"visualizer: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
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
