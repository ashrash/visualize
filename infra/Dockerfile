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
# can install them in a cacheable layer, then add the wheel with --no-deps.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /requirements.txt \
    && uv build --wheel --out-dir /dist \
    && python -c "import glob, zipfile; z = zipfile.ZipFile(glob.glob('/dist/*.whl')[0]); assert 'viz/static/index.html' in z.namelist(), 'wheel has no built frontend'"

# ---- 3. runtime ----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=wheel /requirements.txt /tmp/requirements.txt
# psycopg is the Postgres driver for the AWS-parity metadata DB.
# TODO(lead): make it a `postgres` extra in pyproject.toml instead of installing it here.
RUN uv pip install -r /tmp/requirements.txt "psycopg[binary]>=3.2"
COPY --from=wheel /dist/ /tmp/dist/
RUN uv pip install --no-deps /tmp/dist/*.whl && rm -rf /tmp/dist /tmp/requirements.txt

RUN useradd --create-home --uid 10001 viz && mkdir /data && chown viz /data
USER viz
ENV VIZ_DATA_DIR=/data VIZ_HOST=0.0.0.0 VIZ_PORT=8000
VOLUME /data
EXPOSE 8000
# /api/projects touches the metadata DB, so this also proves the DB connection works.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/projects', timeout=4)"]
CMD ["viz", "serve", "--no-open"]
