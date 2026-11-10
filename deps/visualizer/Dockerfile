# syntax=docker/dockerfile:1
# The visualizer service: the kit's source tree and its locked dependencies, nothing else.
# Built by deps/visualizer/viz.sh (make viz-up); the context is the kit checkout and only the
# files allowed by Dockerfile.dockerignore (next to this file) are sent. No secret is built in:
# the engine token arrives through the environment at run time.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# The browser bundle, from the committed lock file.
FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS web
WORKDIR /web
COPY web/visualizer/package.json web/visualizer/package-lock.json web/visualizer/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY web/visualizer/ ./
RUN npm run build

# The agent's harness CLIs (docs/visualizer-agent.md §6): Claude Code and Codex at the versions pinned,
# with their checksums, in deps/visualizer/agent/package-lock.json. Only their native binaries reach the
# final image, so it needs no Node.js.
FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d AS agent
WORKDIR /agent
COPY deps/visualizer/agent/package.json deps/visualizer/agent/package-lock.json deps/visualizer/agent/.npmrc ./
RUN npm ci --no-audit --no-fund \
 && mkdir -p /out/claude /out/codex \
 && cp node_modules/@anthropic-ai/claude-code/bin/claude.exe /out/claude/claude \
 && cp -r node_modules/@openai/codex-linux-*/vendor/*/. /out/codex/ \
 && /out/claude/claude --version && /out/codex/bin/codex --version

FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
# Dependencies first, so a source change does not reinstall them.
COPY pyproject.toml uv.lock LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
# The harness CLIs. /agent-home is where the named volume is mounted; a new volume takes this directory's
# mode, and the container's user is chosen at run time (compose.yaml), so it is open to that user.
COPY --from=agent /out/claude /opt/claude
COPY --from=agent /out/codex /opt/codex
RUN ln -s /opt/claude/claude /usr/local/bin/claude && ln -s /opt/codex/bin/codex /usr/local/bin/codex \
 && mkdir -m 1777 /agent-home
# The kit is used from this source tree: the config and the engine adapter locate presets/, kit/,
# vendor/ and schemas/ relative to it. The image is the hub (ONTOLOGIZER_HUB=/app).
COPY src ./src
COPY kit ./kit
COPY presets ./presets
COPY vendor ./vendor
COPY schemas ./schemas
COPY ontologizer.yaml ./
COPY --from=web /web/dist ./web/visualizer/dist
RUN uv sync --frozen --no-dev && rm /usr/local/bin/uv && chmod -R a+rX /app
# HOME of the harnesses: the named volume visualizer-agent-home (compose.yaml), where their sign-in lives.
# The service itself never reads it. codex-path holds the tools Codex expects beside it (rg).
ENV ONTOLOGIZER_HUB=/app \
    VISUALIZER_AGENT_HOME=/agent-home \
    PATH=/opt/codex/codex-path:$PATH \
    DISABLE_AUTOUPDATER=1
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8090/visualizer/api/health', headers={'Host': '127.0.0.1:8090'}), timeout=2)"]
ENTRYPOINT ["/app/.venv/bin/ontologizer", "visualizer", "--host", "0.0.0.0", "--port", "8090"]
