# infra

Optional. Running Visualize locally needs only `uv` (no Docker, no Postgres).

- `Dockerfile`: multi-stage image (Node builds the frontend, uv builds the wheel, a slim Python
  runtime installs that wheel). Build from the repo root: `docker build -f infra/Dockerfile -t visualize .`
- `docker-compose.yml`: app + Postgres for **AWS-parity testing of the metadata DB only**.
- `smoke.py`: stdlib-only API smoke test (import CSV, save/autosave project, export/import,
  share token) usable against any running server.

## Wheel smoke test

```
uv run viz build
uv build --wheel
uv venv --python 3.13 /tmp/clean && uv pip install --python /tmp/clean/bin/python dist/visualize-0.1.0-py3-none-any.whl
VIZ_DATA_DIR=/tmp/clean-data /tmp/clean/bin/viz serve --no-open --port 8766 &
uv run python infra/smoke.py --url http://127.0.0.1:8766
```

(On Windows use `Scripts\` instead of `bin/`.)

## Compose / Postgres test

```
docker compose -f infra/docker-compose.yml up --build -d
uv run python infra/smoke.py
docker compose -f infra/docker-compose.yml down -v
```
