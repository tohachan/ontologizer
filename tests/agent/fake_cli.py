"""A stand-in for a harness CLI: replays recorded JSON events (tests/fixtures/agent/).

    fake_cli.py MODE FIXTURE RECORD -- <the real harness's arguments>

MODE is `replay`, `live` (replay, calling the MCP endpoint), `hang` or `crash`. RECORD receives the arguments, stdin and environment names it got.
In `live`, before replaying an `apply_view` result, it calls the view MCP endpoint named in the arguments, as the real
harness would, so that the service has a view event to interleave.
"""

import json
import os
import sys
import time
import tomllib
import urllib.request

mode, fixture, record = sys.argv[1:4]
rest = sys.argv[5:]


def opt(name):
    return rest[rest.index(name) + 1] if name in rest else None


claude = "--input-format" in rest
stdin = sys.stdin.readline() if claude else sys.stdin.read()
json.dump({"argv": rest, "stdin": stdin, "env": sorted(os.environ), "cwd": os.getcwd()}, open(record, "w"))

url = header = None
if opt("--mcp-config"):
    server = json.load(open(opt("--mcp-config")))["mcpServers"]["view"]
    url, header = server["url"], server["headers"]["Authorization"]
for i, a in enumerate(rest):
    if a == "-c" and rest[i + 1].startswith("mcp_servers.view.url="):
        url = tomllib.loads("v = " + rest[i + 1].split("=", 1)[1])["v"]
        header = "Bearer " + os.environ.get("ONTOLOGIZER_VIEW_MCP_TOKEN", "")


def call_apply(args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "apply_view", "arguments": args}}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Authorization": header,
                                             "Host": url.split("/")[2]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open(req, timeout=10).read()


if mode == "hang":
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "hang-1"}), flush=True)
    time.sleep(600)
if mode == "crash":
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "crash-1"}), flush=True)
    sys.stderr.write("fatal: the model API refused the key sk-abcdefghijklmnopqrstuvwxyz0123456789\n")
    sys.exit(3)

calls = {}
for line in open(fixture):
    e = json.loads(line)
    if e.get("type") == "assistant":
        for b in e["message"]["content"]:
            if b.get("type") == "tool_use":
                calls[b["id"]] = (b["name"], b["input"])
    if mode == "live" and e.get("type") == "user":
        for b in e["message"]["content"]:
            name, args = calls.get(b.get("tool_use_id"), ("", {}))
            if name.endswith("apply_view") and not b.get("is_error"):
                call_apply(args)
    item = e.get("item") or {}
    if mode == "live" and e.get("type") == "item.completed" and item.get("tool") == "apply_view" and item.get("status") == "completed":
        call_apply(item["arguments"])
    sys.stdout.write(line)
    sys.stdout.flush()
if claude:
    sys.stdin.read()                       # Claude waits for its input to end after the result
