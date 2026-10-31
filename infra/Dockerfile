# syntax=docker/dockerfile:1
#
# Multi-stage image for Visualize. Build from the repo root:
#   docker build -f infra/Dockerfile -t visualize .
#
# Stages: frontend (Node) -> wheel (uv) -> runtime (slim Python, installs the wheel). The runtime
# stage installs the same wheel a real user gets from `uv build`, so this exercises the
# packaging path. Stateless: all state lives under VIZ_DATA_DIR (/data) or in the metadata DB
# named by VIZ_DATABASE_URL; nothing else is written to the image filesystem.

# ---- 1. frontend ---------------------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm --prefix frontend ci
COPY frontend/ frontend/
# vite.config.ts writes to ../backend/src/viz/static
RUN mkdir -p backend/src/viz && npm --prefix frontend run build

# ---- 2. wheel ------------------------------------------------------------------------------
FROM python:3.13-slim AS wheel
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY backend/src backend/src
COPY --from=frontend /src/backend/src/viz/static backend/src/viz/static
# Pinned third-party requirements (from uv.lock) are exported separately so the runtime stage
# can install them in a cacheable layer, then add the wheel with --no-deps. `--extra postgres`
# pulls psycopg into this same export: `uv pip install --no-deps` on the wheel later would
# otherwise skip an extra's dependencies too, not just the base ones (confirmed: psycopg silently
# missing from the image when the wheel install used `<wheel>[postgres]` with --no-deps).
RUN uv export --frozen --no-dev --no-emit-project --no-hashes --extra postgres -o /requirements.txt \
    && uv build --wheel --out-dir /dist \
    && python -c "import glob, zipfile; z = zipfile.ZipFile(glob.glob('/dist/*.whl')[0]); assert 'viz/static/index.html' in z.namelist(), 'wheel has no built frontend'"

# ---- 3. runtime ----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=wheel /requirements.txt /tmp/requirements.txt
RUN uv pip install -r /tmp/requirements.txt
COPY --from=wheel /dist/ /tmp/dist/
RUN uv pip install --no-deps /tmp/dist/*.whl && rm -rf /tmp/dist /tmp/requirements.txt

RUN useradd --create-home --uid 10001 viz && mkdir /data && chown viz /data
USER viz
ENV VIZ_DATA_DIR=/data VIZ_HOST=0.0.0.0 VIZ_PORT=8000
VOLUME /data
EXPOSE 8000
# /api/health is the one deliberately unauthenticated route (viz/auth/__init__.py): a load
# balancer probe can't carry credentials. It also runs a trivial query, so this proves the
# metadata DB connection works too.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
CMD ["viz", "serve", "--no-open"]
