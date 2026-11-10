# syntax=docker/dockerfile:1
# The visualizer service: the kit's source tree and its locked dependencies, nothing else.
# Built by deps/visualizer/viz.sh (make viz-up); the context is the kit checkout and only the
# files allowed by Dockerfile.dockerignore (next to this file) are sent. No secret is built in:
# the engine token arrives through the environment at run time.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

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
# The kit is used from this source tree: the config and the engine adapter locate presets/, kit/,
# vendor/ and schemas/ relative to it. The image is the hub (ONTOLOGIZER_HUB=/app).
COPY src ./src
COPY kit ./kit
COPY presets ./presets
COPY vendor ./vendor
COPY schemas ./schemas
COPY ontologizer.yaml ./
RUN uv sync --frozen --no-dev && rm /usr/local/bin/uv && chmod -R a+rX /app
ENV ONTOLOGIZER_HUB=/app
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8090/visualizer/api/health', headers={'Host': '127.0.0.1:8090'}), timeout=2)"]
ENTRYPOINT ["/app/.venv/bin/ontologizer", "visualizer", "--host", "0.0.0.0", "--port", "8090"]
