"""End-to-end performance budgets (CLAUDE.md section 6), measured over HTTP against a real server.

    uv run python scripts/bench.py                 # 10M rows x 20 columns (generates the fixture)
    uv run python scripts/bench.py --quick         # 1M rows, a few minutes less
    uv run python scripts/bench.py --url http://127.0.0.1:8000 --rows 10000000

It starts `viz serve` (same as `uv run viz serve --no-open`) on a free port with a throwaway
VIZ_DATA_DIR, or targets an existing server with --url (then the fixture path must be readable by
that server). The fixture comes from scripts/gen_large_dataset.py and is generated when missing.
Prints an ASCII table (metric, budget, measured, result) and exits 1 on a regression.

Budgets measured (CLAUDE.md section 6, laptop, 10M rows x 20 columns):
  CSV import <= 60 s | grid page <= 150 ms (unsorted, sorted, filtered, deep offset)
  aggregated chart query p50 <= 300 ms | chart payload <= 50k marks and <= 5 MB
  6-chart dashboard refresh with one shared filter <= 1 s, idle and during a 10M-row import
  12 concurrent chart queries finish without errors or OOM
  descriptive stats for 10 fields with modes <= 3 s, ungrouped (n/a until /api/stats/describe
  exists); grouped describe and the M4 raw-timestamp line chart have no formal budget but are
  tracked against their own ceilings (KNOWN_ISSUES / run_describe_grouped)

Results marked "known" are failures already tracked in docs/STATUS.md; they are shown as FAIL
(known) and do not change the exit code unless --strict is given.
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import math
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.ipc as ipc

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DEFAULT_FIXTURE_DIR = REPO_ROOT / "data" / "perf"

# --- budgets (CLAUDE.md section 6) -------------------------------------------------------------
IMPORT_BUDGET_S = 60.0
PAGE_BUDGET_MS = 150.0
CHART_P50_BUDGET_MS = 300.0
MARK_CAP = 50_000
PAYLOAD_CAP_BYTES = 5 * 1024 * 1024
DASHBOARD_BUDGET_S = 1.0
DESCRIBE_BUDGET_S = 3.0

# Failures that docs/STATUS.md already tracks (they do not fail the run unless --strict).
_LARGE_DATASETS = 'STATUS MVP 1 "Large dataset handling" (data-core)'


@dataclass(frozen=True)
class Known:
    """A tracked miss: tolerated up to `ceiling_ms` (about 1.5x the tracked number)."""

    ceiling_ms: float
    note: str


KNOWN_ISSUES = {
    # Keys are the case labels used by Bench.cold_case / grid_case (10M-row numbers).
    # "filtered range, deep offset, cold" met budget on a quiet machine (48-60 ms) and was removed.
    # "sorted, cold" stays: quiet-machine runs measured 130-160 ms, a hair over the 150 ms budget.
    "sorted, cold": Known(
        250,
        f"{_LARGE_DATASETS}: first sorted page ~130-160 ms cold, quiet machine (ceiling 250 ms)",
    ),
    # Tighter than the ~1.5x convention on purpose: data-core asked for this exact ceiling, and
    # the quiet-machine max (405-528 ms) sits close under it. Not in CI, so a flake here just
    # means a re-run, not a blocked pipeline.
    "sorted, deep offset, cold": Known(
        750,
        f"{_LARGE_DATASETS}: deep sorted page, view not yet built ~0.4-0.55 s (ceiling 0.75 s)",
    ),
    "search, free text": Known(
        300, f"{_LARGE_DATASETS}: free-text search ~0.15-0.18 s, up to ~0.3 s (ceiling 0.3 s)"
    ),
    # Raw-timestamp M4 line: p50 260-300 ms on a quiet machine, i.e. right at the 300 ms budget.
    "chart aggregate line: order_ts vs score (M4)": Known(
        400, "transform: M4 over 10M raw timestamps, p50 ~260-300 ms quiet (ceiling 400 ms)"
    ),
}

TERMINAL_JOB = {"succeeded", "failed", "cancelled"}


# --- results -----------------------------------------------------------------------------------


@dataclass
class Result:
    metric: str
    budget: str
    measured: str
    status: str  # PASS | FAIL | WARN | N/A
    note: str = ""
    known: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


def verdict(ok: bool, key: str | None = None, value_ms: float | None = None) -> tuple[str, bool]:
    """(status, known). A tracked miss is 'known' only while it stays under its ceiling."""
    if ok:
        return "PASS", False
    known = KNOWN_ISSUES.get(key) if key else None
    return "FAIL", known is not None and value_ms is not None and value_ms <= known.ceiling_ms


def known_note(key: str, known: bool, value_ms: float) -> str:
    entry = KNOWN_ISSUES.get(key)
    if entry is None:
        return ""
    if known:
        return entry.note
    return (
        f"ABOVE the tracked ceiling of {entry.ceiling_ms:.0f} ms ({value_ms:,.0f} ms). {entry.note}"
    )


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def summarize(values: Sequence[float]) -> dict[str, float]:
    return {
        "n": float(len(values)),
        "min": min(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def fmt_ms(value: float) -> str:
    return f"{value:,.0f} ms" if value >= 10 else f"{value:.1f} ms"


def stat_text(stats: dict[str, float], stat: str) -> str:
    return f"{stat} {fmt_s(stats[stat])} (p50 {fmt_s(stats['p50'])}, max {fmt_s(stats['max'])})"


def fmt_s(value: float) -> str:
    return f"{value:.2f} s"


# --- server lifecycle --------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Server:
    """`viz serve` as a child process with its own data dir (or an external --url)."""

    def __init__(self, url: str | None, port: int | None) -> None:
        self.external = url is not None
        self.data_dir: Path | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.log_path: Path | None = None
        self.log_handle: Any = None
        self.port = port or (0 if self.external else free_port())
        self.url = url.rstrip("/") if url else f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self.external:
            self.wait_ready()
            return
        self.data_dir = Path(tempfile.mkdtemp(prefix="viz-bench-"))
        env = os.environ.copy()
        env["VIZ_DATA_DIR"] = str(self.data_dir)
        env["PYTHONUNBUFFERED"] = "1"
        self.log_path = self.data_dir / "server.log"
        log = self.log_handle = self.log_path.open("wb")
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        # Same entry point as `uv run viz serve --no-open --port N`, without needing uv on PATH.
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "viz.cli", "serve", "--no-open", "--port", str(self.port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        self.wait_ready()

    def wait_ready(self, timeout_s: float = 90.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(
                    f"server exited early (code {self.proc.returncode})\n{self.tail()}"
                )
            with suppress(httpx.HTTPError):
                if httpx.get(f"{self.url}/api/health", timeout=2).status_code == 200:
                    return
            time.sleep(0.3)
        raise RuntimeError(f"server did not become ready in {timeout_s:.0f} s\n{self.tail()}")

    def tail(self, lines: int = 25) -> str:
        if self.log_path and self.log_path.is_file():
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        return ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                import signal

                with suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal

                    with suppress(ProcessLookupError):
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    with suppress(subprocess.TimeoutExpired):
                        self.proc.wait(timeout=10)
        if self.log_handle is not None:
            with suppress(OSError):
                self.log_handle.close()
        if self.data_dir is not None:
            for _ in range(10):
                try:
                    shutil.rmtree(self.data_dir)
                    return
                except OSError:
                    time.sleep(0.5)  # Windows still releases file handles
            print(f"warning: could not remove the bench data dir {self.data_dir}")


class PeakMemory:
    """Peak resident memory of the server process tree (psutil is a dev dependency).

    Needs a server this script started (a pid); with --url it reports nothing. `baseline` is the
    resident size when sampling began, `total` the machine's RAM, for context.
    """

    def __init__(self, pid: int | None) -> None:
        self.pid = pid
        self.peak = 0
        self.baseline = 0
        self.total = 0
        self.available = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        import psutil

        try:
            root = psutil.Process(self.pid)
        except psutil.Error:
            return
        self.total = psutil.virtual_memory().total
        while not self._stop.is_set():
            try:
                total = sum(p.memory_info().rss for p in [root, *root.children(recursive=True)])
                self.baseline = self.baseline or total
                self.peak = max(self.peak, total)
            except psutil.Error:
                pass
            self._stop.wait(0.05)

    def describe(self) -> str:
        if not self.available or not self.peak:
            return "peak RSS n/a"
        gib = 2**30
        share = f" = {100 * self.peak / self.total:.0f}% of RAM" if self.total else ""
        return (
            f"peak RSS {self.peak / gib:.2f} GiB (+{(self.peak - self.baseline) / gib:.2f} GiB "
            f"over start){share}"
        )

    def __enter__(self) -> PeakMemory:
        if self.pid is not None:
            try:
                import psutil  # noqa: F401
            except ImportError:
                return self
            self.available = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


# --- API helpers -------------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )

    def close(self) -> None:
        self.client.close()

    def openapi_paths(self) -> set[str]:
        response = self.client.get("/openapi.json")
        response.raise_for_status()
        return set(response.json().get("paths", {}))

    def start_import(self, path: Path, name: str) -> dict[str, Any]:
        response = self.client.post("/api/datasets/import", json={"path": str(path), "name": name})
        response.raise_for_status()
        return dict(response.json())

    def job(self, job_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        return dict(response.json())

    def wait_job(self, job_id: str, timeout_s: float = 900.0) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while True:
            job = self.job(job_id)
            if job["status"] in TERMINAL_JOB:
                return job
            if time.time() > deadline:
                raise TimeoutError(f"job {job_id} still {job['status']}")
            time.sleep(0.1)

    def cancel_job(self, job_id: str) -> None:
        with suppress(httpx.HTTPError):
            self.client.post(f"/api/jobs/{job_id}/cancel")

    def delete_dataset(self, dataset_id: str) -> None:
        with suppress(httpx.HTTPError):
            self.client.delete(f"/api/datasets/{dataset_id}", params={"force": "true"})

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        response = self.client.get(f"/api/datasets/{dataset_id}")
        response.raise_for_status()
        return dict(response.json())

    def rows(self, dataset_id: str, body: dict[str, Any]) -> tuple[float, int, httpx.Response]:
        """POST rows; returns (milliseconds, response bytes, response)."""
        started = time.perf_counter()
        response = self.client.post(f"/api/datasets/{dataset_id}/rows", json=body)
        elapsed = (time.perf_counter() - started) * 1000
        return elapsed, len(response.content), response

    def chart(self, body: dict[str, Any]) -> tuple[float, httpx.Response]:
        started = time.perf_counter()
        response = self.client.post("/api/charts/data", json=body)
        return (time.perf_counter() - started) * 1000, response


def arrow_meta(response: httpx.Response) -> tuple[pa.Table, dict[str, Any]]:
    reader = ipc.open_stream(io.BytesIO(response.content))
    table = reader.read_all()
    raw = (table.schema.metadata or {}).get(b"viz.meta", b"{}")
    return table, dict(json.loads(raw))


# --- request builders --------------------------------------------------------------------------


def enc(
    field_name: str | None, agg: str | None = None, time_unit: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if field_name is not None:
        out["field"] = field_name
    if agg is not None:
        out["agg"] = agg
    if time_unit is not None:
        out["timeUnit"] = time_unit
    return out


def chart_body(
    dataset_id: str,
    plot: str,
    encodings: dict[str, list[dict[str, Any]]],
    *,
    filters: Sequence[dict[str, Any]] = (),
    chart_id: str | None = None,
) -> dict[str, Any]:
    """A /api/charts/data request. Each concurrent chart needs its own id: the engine cancels an
    older in-flight query that has the same chart id (cross-filter supersede)."""
    return {
        "spec": {
            "id": chart_id or str(uuid.uuid4()),
            "name": "bench",
            "plotType": plot,
            "source": {"kind": "dataset", "id": dataset_id},
            "encodings": encodings,
        },
        "filters": list(filters),
        "viewport": {"widthPx": 1200, "heightPx": 400},
    }


_BUST = itertools.count(1)


def bust(rows: int) -> dict[str, Any]:
    """A filter that scans nearly the same data but never repeats within a run (one global,
    ever-increasing counter), so no result cache can answer it."""
    return {"op": "range", "field": "order_id", "min": 1, "max": max(1, rows - next(_BUST))}


@dataclass
class ChartCase:
    label: str
    plot: str
    encodings: dict[str, list[dict[str, Any]]]


AGGREGATE_CASES = [
    ChartCase("bar: region, sum(sales)", "bar", {"x": [enc("region")], "y": [enc("sales", "sum")]}),
    ChartCase(
        "bar: category, sum(sales)+mean(price)",
        "bar",
        {"x": [enc("category")], "y": [enc("sales", "sum"), enc("unit_price", "mean")]},
    ),
    ChartCase(
        "bar: country, mean(unit_price)",
        "bar",
        {"x": [enc("country")], "y": [enc("unit_price", "mean")]},
    ),
    ChartCase(
        "line: month(order_date), sum(sales)",
        "line",
        {"x": [enc("order_date", time_unit="month")], "y": [enc("sales", "sum")]},
    ),
    ChartCase(
        "stacked: region x channel, count",
        "stacked_bar",
        {"x": [enc("region")], "y": [enc(None, "count")], "color": [enc("channel")]},
    ),
    # Raw-timestamp series path (M4 downsampling), cache-busted like the others.
    ChartCase(
        "line: order_ts vs score (M4)", "line", {"x": [enc("order_ts")], "y": [enc("score")]}
    ),
]

DASHBOARD_CASES = [
    ChartCase("bar: region", "bar", {"x": [enc("region")], "y": [enc("sales", "sum")]}),
    ChartCase("bar: category", "bar", {"x": [enc("category")], "y": [enc(None, "count")]}),
    ChartCase(
        "line: month",
        "line",
        {"x": [enc("order_date", time_unit="month")], "y": [enc("sales", "sum")]},
    ),
    ChartCase("pie: channel", "pie", {"x": [enc("channel")], "y": [enc("units", "sum")]}),
    ChartCase("bar: segment", "bar", {"x": [enc("segment")], "y": [enc("sales", "mean")]}),
    ChartCase("bar: product", "bar", {"x": [enc("product")], "y": [enc("sales", "sum")]}),
]

HEAVY_CASES = [
    ChartCase("customer_id sum", "bar", {"x": [enc("customer_id")], "y": [enc("sales", "sum")]}),
    ChartCase("sku sum", "bar", {"x": [enc("sku")], "y": [enc("sales", "sum")]}),
    ChartCase("product sum", "bar", {"x": [enc("product")], "y": [enc("sales", "sum")]}),
    ChartCase("country mean", "bar", {"x": [enc("country")], "y": [enc("unit_price", "mean")]}),
    ChartCase(
        "category 3 aggs",
        "bar",
        {
            "x": [enc("category")],
            "y": [enc("sales", "sum"), enc("units", "mean"), enc("unit_price", "max")],
        },
    ),
    ChartCase(
        "region x channel",
        "stacked_bar",
        {"x": [enc("region")], "y": [enc("sales", "sum")], "color": [enc("channel")]},
    ),
    ChartCase(
        "month line",
        "line",
        {"x": [enc("order_date", time_unit="month")], "y": [enc("sales", "sum")]},
    ),
    ChartCase(
        "day line", "line", {"x": [enc("order_date", time_unit="day")], "y": [enc("units", "sum")]}
    ),
    ChartCase(
        "segment x channel",
        "grouped_bar",
        {"x": [enc("segment")], "y": [enc(None, "count")], "color": [enc("channel")]},
    ),
    ChartCase("customer_id count", "bar", {"x": [enc("customer_id")], "y": [enc(None, "count")]}),
    ChartCase("sku mean", "bar", {"x": [enc("sku")], "y": [enc("unit_price", "mean")]}),
    ChartCase(
        "country x region",
        "stacked_bar",
        {"x": [enc("country")], "y": [enc("units", "sum")], "color": [enc("region")]},
    ),
]


# --- scenarios ---------------------------------------------------------------------------------


class Bench:
    def __init__(self, api: Api, server: Server, args: argparse.Namespace, csv_path: Path) -> None:
        self.api = api
        self.server = server
        self.args = args
        self.csv_path = csv_path
        self.rows: int = args.rows
        self.results: list[Result] = []
        self.dataset_id = ""
        self.log = lambda message: print(f"  .. {message}", flush=True)
        self._rounds = itertools.count(1)

    def add(self, result: Result) -> Result:
        self.results.append(result)
        return result

    # -- import --------------------------------------------------------------------------------

    def import_budget(self) -> tuple[float, str]:
        scaled = IMPORT_BUDGET_S * self.rows / 10_000_000
        if self.rows >= 10_000_000:
            return IMPORT_BUDGET_S, f"<= {IMPORT_BUDGET_S:.0f} s"
        return scaled, f"<= {scaled:.1f} s (scaled)"

    def run_import(self) -> None:
        budget_s, budget_text = self.import_budget()
        started = time.perf_counter()
        job = self.api.start_import(self.csv_path, f"bench_{self.rows}")
        done = self.api.wait_job(job["id"])
        elapsed = time.perf_counter() - started
        size_mb = self.csv_path.stat().st_size / 2**20 if self.csv_path.exists() else 0
        if done["status"] != "succeeded":
            self.add(
                Result(
                    f"CSV import {self.rows:,} x 20",
                    budget_text,
                    f"job {done['status']}",
                    "FAIL",
                    note=str(done.get("error")),
                )
            )
            raise RuntimeError(f"import failed: {done.get('error')}")
        self.dataset_id = done["result"]["datasetId"]
        dataset = self.api.dataset(self.dataset_id)
        loaded = dataset["versions"][-1]["rowCount"]
        ok = elapsed <= budget_s and loaded == self.rows
        status, known = verdict(ok)
        self.add(
            Result(
                f"CSV import {self.rows:,} rows x 20 cols",
                budget_text,
                fmt_s(elapsed),
                status,
                note=f"{size_mb:,.0f} MiB CSV, {loaded:,} rows loaded",
                known=known,
                data={"seconds": elapsed, "rows": loaded, "csvMiB": size_mb},
            )
        )

    # -- grid pages ----------------------------------------------------------------------------

    def page_body(self, **extra: Any) -> dict[str, Any]:
        return {"limit": 200, **extra}

    def measure_pages(self, bodies: Sequence[dict[str, Any]]) -> list[float]:
        times = []
        for body in bodies:
            ms, _, response = self.api.rows(self.dataset_id, body)
            if response.status_code != 200:
                raise RuntimeError(
                    f"rows request failed: {response.status_code} {response.text[:200]}"
                )
            times.append(ms)
        return times

    def grid_case(self, label: str, bodies: Sequence[dict[str, Any]], note: str = "") -> None:
        # Gated on p95 (needs >= 10 repetitions, so p95 is not just the max of a handful).
        times = self.measure_pages(bodies)
        stats = summarize(times)
        status, known = verdict(stats["p95"] <= PAGE_BUDGET_MS, label, stats["p95"])
        self.add(
            Result(
                f"grid {label}",
                f"<= {PAGE_BUDGET_MS:.0f} ms (p95)",
                f"p95 {fmt_ms(stats['p95'])} (p50 {fmt_ms(stats['p50'])}, n={len(times)})",
                status,
                note=note or (known_note(label, known, stats["p95"]) if status == "FAIL" else ""),
                known=known,
                data=stats,
            )
        )

    def cold_case(self, label: str, body: dict[str, Any]) -> None:
        ms = self.measure_pages([body])[0]
        status, known = verdict(ms <= PAGE_BUDGET_MS, label, ms)
        self.add(
            Result(
                f"grid {label}",
                f"<= {PAGE_BUDGET_MS:.0f} ms",
                fmt_ms(ms),
                status,
                note=known_note(label, known, ms) if status == "FAIL" else "",
                known=known,
                data={"ms": ms},
            )
        )

    def wait_for_view(self, bodies: Sequence[dict[str, Any]], cap_s: float) -> float:
        """Poll later pages of the same sort/filter until all answer in < 50 ms (the background
        view is built) or `cap_s` passes. Returns the seconds waited."""
        started = time.perf_counter()
        while time.perf_counter() - started < cap_s:
            if all(t < 50 for t in self.measure_pages(bodies)):
                break
            time.sleep(0.5)
        return time.perf_counter() - started

    def run_grid(self) -> None:
        n = self.rows
        reps = self.args.reps
        self.log("grid pages: unsorted")
        self.grid_case(
            "unsorted, first page", [self.page_body(offset=200 * k) for k in range(reps)]
        )
        self.grid_case(
            "unsorted, deep offset (middle)",
            [self.page_body(offset=n // 2 + 200 * k) for k in range(reps)],
        )
        self.grid_case(
            "unsorted, last page", [self.page_body(offset=n - 200 - 200 * k) for k in range(reps)]
        )

        # Sorted and filtered: the first request is a direct query (cold); scrolling queues a view
        # build in the background; later pages are range reads. Measure both phases.
        build_wait_s = max(3.0, 8.0 * n / 10_000_000)
        sorted_by = [{"field": "sales", "descending": True}]
        self.log("grid pages: sorted")
        self.cold_case("sorted, cold", self.page_body(sort=sorted_by, offset=0))
        self.measure_pages([self.page_body(sort=sorted_by, offset=200)])  # scroll: queues the build
        self.wait_for_view(
            [
                self.page_body(sort=sorted_by, offset=400),
                self.page_body(sort=sorted_by, offset=n // 2),
            ],
            3 * build_wait_s,
        )
        self.grid_case(
            "sorted, warm (after view build)",
            [self.page_body(sort=sorted_by, offset=400 + 200 * k) for k in range(reps)],
        )
        self.grid_case(
            "sorted, deep offset (warm)",
            [self.page_body(sort=sorted_by, offset=n // 2 + 200 * k) for k in range(reps)],
        )

        self.log("grid pages: filtered")
        category_filter = [{"op": "eq", "field": "category", "value": "Category 03"}]
        self.cold_case("filtered, cold", self.page_body(filters=category_filter, offset=0))
        self.measure_pages([self.page_body(filters=category_filter, offset=200)])
        self.wait_for_view([self.page_body(filters=category_filter, offset=400)], 3 * build_wait_s)
        self.grid_case(
            "filtered, warm (after view build)",
            [self.page_body(filters=category_filter, offset=400 + 200 * k) for k in range(reps)],
        )
        range_filter = [{"op": "range", "field": "units", "min": 2, "max": 9}]
        # Jumping straight to a deep page of a sort or filter nobody has used yet: no view exists,
        # so the server answers with a full sort/filter scan.
        self.cold_case(
            "filtered range, deep offset, cold",
            self.page_body(filters=range_filter, offset=n // 4),
        )
        self.cold_case(
            "sorted, deep offset, cold",
            self.page_body(sort=[{"field": "score", "descending": False}], offset=n // 2),
        )
        # Free-text search over the text columns (tracked in STATUS as ~150-180 ms at 10M quiet).
        self.cold_case("search, free text", self.page_body(search="Product 01"))
        # Let the background view builds those queries triggered finish before the next scenario.
        self.wait_for_view(
            [self.page_body(sort=[{"field": "score", "descending": False}], offset=n // 2 + 200)],
            3 * build_wait_s,
        )

    # -- charts --------------------------------------------------------------------------------

    def chart_request(
        self,
        case: ChartCase,
        k: int,
        chart_id: str | None = None,
        extra_filters: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        return chart_body(
            self.dataset_id,
            case.plot,
            case.encodings,
            filters=[bust(self.rows), *extra_filters],
            chart_id=chart_id,
        )

    def run_chart_aggregates(self) -> None:
        reps = self.args.chart_reps
        for case in AGGREGATE_CASES:
            self.log(f"chart aggregate: {case.label}")
            times: list[float] = []
            cached = 0
            sample_bytes = 0
            error = ""
            for k in range(reps + 2):
                ms, response = self.api.chart(
                    self.chart_request(case, k, chart_id=f"agg-{case.label}")
                )
                if response.status_code != 200:
                    error = f"HTTP {response.status_code}: {response.text[:160]}"
                    break
                if k < 2:
                    continue  # warm-up: file cache and Parquet metadata
                _, meta = arrow_meta(response)
                cached += bool(meta.get("cached"))
                sample_bytes = len(response.content)
                times.append(ms)
            if error:
                self.add(
                    Result(
                        f"chart aggregate {case.label}",
                        f"p50 <= {CHART_P50_BUDGET_MS:.0f} ms",
                        "error",
                        "FAIL",
                        note=error,
                    )
                )
                continue
            stats = summarize(times)
            key = f"chart aggregate {case.label}"
            status, known = verdict(stats["p50"] <= CHART_P50_BUDGET_MS, key, stats["p50"])
            base_note = f"{sample_bytes / 1024:.1f} KiB, {cached}/{len(times)} cached"
            note = (
                f"{base_note}; {known_note(key, known, stats['p50'])}"
                if status == "FAIL" and known_note(key, known, stats["p50"])
                else base_note
            )
            self.add(
                Result(
                    key,
                    f"p50 <= {CHART_P50_BUDGET_MS:.0f} ms",
                    f"p50 {fmt_ms(stats['p50'])} (p95 {fmt_ms(stats['p95'])})",
                    status,
                    note=note,
                    known=known,
                    data={**stats, "cached": cached, "bytes": sample_bytes},
                )
            )

    def run_payload_caps(self) -> None:
        cases = [
            ChartCase(
                f"bar: customer_id (~{max(1_000, self.rows // 20):,} groups)",
                "bar",
                {"x": [enc("customer_id")], "y": [enc("sales", "sum")]},
            ),
            # `line` is a series plot (M4/LTTB downsampling) and is implemented; with no agg and
            # no timeUnit it must reduce 10M raw points to the cap.
            ChartCase(
                "line (series): order_ts vs score (all rows)",
                "line",
                {"x": [enc("order_ts")], "y": [enc("score")]},
            ),
            # lat/lon carry the geo semantic, which chart-data rejects for x/y (HTTP 422), so use
            # two quantitative columns to exercise the scatter payload-cap path.
            ChartCase(
                "scatter: unit_price vs score (all rows)",
                "scatter",
                {"x": [enc("unit_price")], "y": [enc("score")]},
            ),
            ChartCase(
                "timeseries_line: order_ts vs score (all rows)",
                "timeseries_line",
                {"x": [enc("order_ts")], "y": [enc("score")]},
            ),
        ]
        for case in cases:
            body = chart_body(
                self.dataset_id, case.plot, case.encodings, chart_id=f"cap-{case.plot}"
            )
            ms, response = self.api.chart(body)
            name = f"payload cap {case.label}"
            budget = f"<= {MARK_CAP // 1000}k marks, <= {PAYLOAD_CAP_BYTES // 2**20} MB"
            if response.status_code == 501:
                self.add(
                    Result(
                        name,
                        budget,
                        "n/a (plot not implemented)",
                        "N/A",
                        note=(
                            "501: this plot is not implemented in the chart-data endpoint yet "
                            "(transform); the row activates by itself once it answers"
                        ),
                    )
                )
                continue
            if response.status_code != 200:
                self.add(
                    Result(
                        name,
                        budget,
                        f"HTTP {response.status_code}",
                        "FAIL",
                        note=f"{response.text[:160]} (does the request match the plot's channels?)",
                    )
                )
                continue
            if response.headers.get("content-type", "").startswith("application/json"):
                # A stat-transform plot answers with a JSON StatResult instead of an Arrow table.
                size = len(response.content)
                status, known = verdict(size <= PAYLOAD_CAP_BYTES)
                self.add(
                    Result(
                        name,
                        budget,
                        f"JSON result, {size / 2**20:.2f} MB",
                        status,
                        note=fmt_ms(ms),
                        known=known,
                        data={"bytes": size, "ms": ms},
                    )
                )
                continue
            table, meta = arrow_meta(response)
            marks = table.num_rows * max(1, sum(1 for c in table.column_names if c.startswith("y")))
            size = len(response.content)
            ok = table.num_rows <= MARK_CAP and size <= PAYLOAD_CAP_BYTES
            status, known = verdict(ok)
            self.add(
                Result(
                    name,
                    budget,
                    f"{table.num_rows:,} rows, {size / 2**20:.2f} MB",
                    status,
                    note=f"reduction={meta.get('reduction')}, {fmt_ms(ms)}, marks~{marks:,}",
                    known=known,
                    data={
                        "rows": table.num_rows,
                        "bytes": size,
                        "ms": ms,
                        "reduction": meta.get("reduction"),
                    },
                )
            )

    # -- concurrency ---------------------------------------------------------------------------

    def post_chart(self, body: dict[str, Any]) -> tuple[int, bool | None]:
        """POST chart data; returns (status, meta.cached or None when the body has no meta)."""
        response = self.api.client.post("/api/charts/data", json=body)
        if response.status_code != 200:
            return response.status_code, None
        if response.headers.get("content-type", "").startswith("application/json"):
            return 200, dict(response.json().get("meta", {})).get("cached")
        return 200, arrow_meta(response)[1].get("cached")

    def dashboard_round(self, pool: ThreadPoolExecutor) -> tuple[float, int, int]:
        """Six charts sharing one cross-filter. Every round gets a new country value and a new
        bust() filter, so nothing can be answered from the result cache.
        Returns (seconds, request errors, responses that claim meta.cached)."""
        round_no = next(self._rounds)
        shared = [
            {"op": "eq", "field": "country", "value": f"Country {round_no % 40 + 1:02d}"},
            bust(self.rows),
        ]
        bodies = [
            chart_body(self.dataset_id, c.plot, c.encodings, filters=shared, chart_id=f"dash-{i}")
            for i, c in enumerate(DASHBOARD_CASES)
        ]
        started = time.perf_counter()
        results = list(pool.map(self.post_chart, bodies))
        elapsed = time.perf_counter() - started
        errors = sum(1 for status, _ in results if status != 200)
        cached = sum(1 for _, was_cached in results if was_cached)
        return elapsed, errors, cached

    def dashboard_idle(self) -> None:
        rounds = self.args.dashboard_rounds
        stat = self.args.stat
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.dashboard_round(pool)  # warm-up
            samples, errors, cached = [], 0, 0
            for _ in range(rounds):
                elapsed, failed, hits = self.dashboard_round(pool)
                samples.append(elapsed)
                errors += failed
                cached += hits
        stats = summarize(samples)
        status, known = verdict(stats[stat] <= DASHBOARD_BUDGET_S and errors == 0 and cached == 0)
        self.add(
            Result(
                "dashboard refresh (6 charts, 1 filter), idle",
                f"<= {DASHBOARD_BUDGET_S:.0f} s",
                stat_text(stats, stat),
                status,
                note=f"{len(samples)} rounds, {errors} request errors, {cached} cached responses",
                known=known,
                data={**stats, "errors": errors, "cached": cached},
            )
        )

    def dashboard_during_import(self) -> None:
        stat = self.args.stat
        minimum = self.args.dashboard_rounds
        samples: list[float] = []
        stages: dict[str, list[float]] = {}
        errors = cached = imports = 0
        extra_datasets: list[str] = []
        # Rounds run for the whole life of a second import of the same file (never cancelled), at
        # least `minimum` of them: a further import starts if the first ended too soon.
        with ThreadPoolExecutor(max_workers=6) as pool:
            while (len(samples) < minimum or imports == 0) and imports < 4:
                imports += 1
                job = self.api.start_import(self.csv_path, f"bench_load_{imports}")
                time.sleep(0.3)  # let the job start scanning the file
                while True:
                    state = self.api.job(job["id"])
                    if state["status"] in TERMINAL_JOB:
                        break
                    elapsed, failed, hits = self.dashboard_round(pool)
                    samples.append(elapsed)
                    stages.setdefault(str(state.get("message") or state["status"]), []).append(
                        elapsed
                    )
                    errors += failed
                    cached += hits
                done = self.api.wait_job(job["id"])
                if done.get("result") and done["result"].get("datasetId"):
                    extra_datasets.append(done["result"]["datasetId"])
        for dataset_id in extra_datasets:
            self.api.delete_dataset(dataset_id)
        name = (
            f"dashboard refresh (6 charts, 1 filter), during a {self.rows // 1_000_000}M-row import"
        )
        if not samples:
            self.add(
                Result(
                    name,
                    f"<= {DASHBOARD_BUDGET_S:.0f} s",
                    "no rounds ran",
                    "FAIL",
                    note="the import finished before any round",
                )
            )
            return
        stats = summarize(samples)
        status, known = verdict(stats[stat] <= DASHBOARD_BUDGET_S and errors == 0 and cached == 0)
        by_stage = "; ".join(
            f"{stage}: {len(vals)} rounds, max {fmt_s(max(vals))}" for stage, vals in stages.items()
        )
        self.add(
            Result(
                name,
                f"<= {DASHBOARD_BUDGET_S:.0f} s",
                stat_text(stats, stat),
                status,
                note=(
                    f"{len(samples)} rounds over {imports} import(s), {errors} request errors, "
                    f"{cached} cached responses; by import stage - {by_stage}"
                ),
                known=known,
                data={
                    **stats,
                    "errors": errors,
                    "cached": cached,
                    "imports": imports,
                    "stages": {k: summarize(v) for k, v in stages.items()},
                },
            )
        )

    def twelve_concurrent(self) -> None:
        cases = HEAVY_CASES
        bodies = [
            chart_body(
                self.dataset_id,
                c.plot,
                c.encodings,
                filters=[bust(self.rows)],
                chart_id=f"c12-{i}",
            )
            for i, c in enumerate(cases)
        ]
        statuses: list[int] = []
        details: list[str] = []
        cached = 0
        pid = self.server.proc.pid if self.server.proc else None
        started = time.perf_counter()
        with PeakMemory(pid) as memory, ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(self.post_chart, b) for b in bodies]
            for future, case in zip(futures, cases, strict=True):
                try:
                    code, was_cached = future.result()
                    statuses.append(code)
                    cached += bool(was_cached)
                    if code != 200:
                        details.append(f"{case.label}: HTTP {code}")
                except httpx.HTTPError as exc:
                    statuses.append(0)
                    details.append(f"{case.label}: {type(exc).__name__}")
        elapsed = time.perf_counter() - started
        ok_count = sum(1 for s in statuses if s == 200)
        alive = False
        with suppress(httpx.HTTPError):
            alive = self.api.client.get("/api/health").status_code == 200
        server_alive = alive and (self.server.proc is None or self.server.proc.poll() is None)
        if cached:
            details.append(f"{cached} responses claim meta.cached (cache-busting failed)")
        status, known = verdict(ok_count == len(bodies) and server_alive and cached == 0)
        peak = f"; {memory.describe()}"
        self.add(
            Result(
                "12 concurrent chart queries",
                "all 12 succeed, no OOM",
                f"{ok_count}/12 ok in {fmt_s(elapsed)}",
                status,
                note=("; ".join(details) or "no errors")
                + peak
                + ("" if server_alive else "; SERVER DOWN"),
                known=known,
                data={
                    "ok": ok_count,
                    "seconds": elapsed,
                    "peakRssBytes": memory.peak,
                    "startRssBytes": memory.baseline,
                    "ramBytes": memory.total,
                    "statuses": statuses,
                },
            )
        )

    # -- describe ------------------------------------------------------------------------------

    def run_describe(self, paths: set[str]) -> None:
        name = "describe 10 fields with modes"
        budget = f"<= {DESCRIBE_BUDGET_S:.0f} s"
        if "/api/stats/describe" not in paths:
            self.add(
                Result(
                    name,
                    budget,
                    "n/a (endpoint missing)",
                    "N/A",
                    note="POST /api/stats/describe is not in the OpenAPI schema yet (stats)",
                )
            )
            return
        # DescribeRequest (contracts/stats.py): source, fields, filters, include_mode, ... Ungrouped
        # (no groupBy), modes on (includeMode defaults to true; sent explicitly). The
        # never-repeating filter keeps the result cache from answering.
        fields = [
            "sales",
            "unit_price",
            "units",
            "score",
            "lat",
            "lon",
            "discount",
            "order_date",
            "category",
            "region",
        ]
        times = []
        problems: list[str] = []
        for k in range(11):  # one warm-up, then 10 timed runs (p95 needs the repetitions)
            body = {
                "source": {"kind": "dataset", "id": self.dataset_id},
                "fields": fields,
                "includeMode": True,
                "filters": [bust(self.rows)],
            }
            started = time.perf_counter()
            response = self.api.client.post("/api/stats/describe", json=body)
            elapsed = time.perf_counter() - started
            if response.status_code != 200:
                self.add(
                    Result(
                        name,
                        budget,
                        f"HTTP {response.status_code}",
                        "FAIL",
                        note=response.text[:200],
                    )
                )
                return
            if k == 0:
                continue  # warm-up
            times.append(elapsed)
            result = response.json()
            summaries = result.get("values", {}).get("summaries", [])
            overall = [s for s in summaries if s.get("group") is None]
            if [s.get("field") for s in overall] != fields:
                problems.append(f"summaries for {[s.get('field') for s in overall]}")
            if any(s.get("distinct") is None for s in overall):
                problems.append("modes missing (distinct is null)")
            if result.get("sampled"):
                problems.append("result is sampled, expected exact")
        stats = summarize(times)
        status, known = verdict(stats["p95"] <= DESCRIBE_BUDGET_S and not problems)
        self.add(
            Result(
                name,
                budget + " (p95)",
                f"p95 {fmt_s(stats['p95'])} (p50 {fmt_s(stats['p50'])}, n={len(times)})",
                status,
                note="; ".join(sorted(set(problems))) or "10 fields, ungrouped, modes on, exact",
                known=known,
                data=stats,
            )
        )
        self.run_describe_grouped(fields)

    def run_describe_grouped(self, fields: list[str]) -> None:
        """Grouped describe has no CLAUDE.md budget (Wave 4 item); tracked against a 5 s ceiling."""
        name = "describe 9 fields with modes, grouped by region"
        ceiling_s = 5.0
        grouped_fields = [f for f in fields if f != "region"]
        times = []
        problems: list[str] = []
        for k in range(4):  # one warm-up, then 3 timed runs
            body = {
                "source": {"kind": "dataset", "id": self.dataset_id},
                "fields": grouped_fields,
                "groupBy": ["region"],
                "includeMode": True,
                "filters": [bust(self.rows)],
            }
            started = time.perf_counter()
            response = self.api.client.post("/api/stats/describe", json=body)
            elapsed = time.perf_counter() - started
            if response.status_code != 200:
                self.add(
                    Result(
                        name,
                        f"<= {ceiling_s:.0f} s (tracked)",
                        f"HTTP {response.status_code}",
                        "FAIL",
                        note=response.text[:200],
                    )
                )
                return
            if k == 0:
                continue  # warm-up
            times.append(elapsed)
            result = response.json()
            summaries = result.get("values", {}).get("summaries", [])
            grouped = [s for s in summaries if s.get("group") is not None]
            if not grouped:
                problems.append("no per-group summaries returned")
            if any(s.get("distinct") is None for s in grouped):
                problems.append("modes missing (distinct is null)")
            if result.get("sampled"):
                problems.append("result is sampled, expected exact")
        stats = summarize(times)
        status = "PASS" if stats["max"] <= ceiling_s and not problems else "FAIL"
        self.add(
            Result(
                name,
                f"<= {ceiling_s:.0f} s (tracked, no formal budget)",
                f"p50 {fmt_s(stats['p50'])} (max {fmt_s(stats['max'])}, n={len(times)})",
                status,
                note="; ".join(sorted(set(problems))) or "9 fields, grouped, modes on, exact",
                data=stats,
            )
        )


# --- output ------------------------------------------------------------------------------------


def render_table(results: Sequence[Result]) -> str:
    rows = []
    for r in results:
        status = r.status + (" (known)" if r.failed and r.known else "")
        rows.append((r.metric, r.budget, r.measured, status))
    headers = ("Metric", "Budget", "Measured", "Result")
    widths = [max([len(headers[i]), *(len(row[i]) for row in rows)]) for i in range(4)]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def fmt_row(values: Sequence[str]) -> str:
        return "| " + " | ".join(v.ljust(w) for v, w in zip(values, widths, strict=True)) + " |"

    out = [line, fmt_row(headers), line]
    out += [fmt_row(r) for r in rows]
    out.append(line)
    return "\n".join(out)


def environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpuCount": os.cpu_count(),
        "machine": platform.machine(),
    }


@contextmanager
def timed(label: str) -> Iterator[None]:
    started = time.perf_counter()
    yield
    print(f"  {label}: {time.perf_counter() - started:.1f} s", flush=True)


def ensure_fixture(rows: int, out_dir: Path, seed: int) -> Path:
    csv_path = out_dir / f"large_{rows}.csv"
    if csv_path.is_file():
        return csv_path
    print(f"fixture {csv_path} is missing; generating {rows:,} rows ...", flush=True)
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "gen_large_dataset.py"),
            "--rows",
            str(rows),
            "--out",
            str(out_dir),
            "--seed",
            str(seed),
        ],
        check=True,
    )
    return csv_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--rows", type=int, default=10_000_000, help="fixture rows (default 10,000,000)"
    )
    parser.add_argument("--quick", action="store_true", help="1,000,000 rows and fewer repetitions")
    parser.add_argument(
        "--url", help="test an already running server (it must be able to read the fixture path)"
    )
    parser.add_argument("--port", type=int, help="port for the server this script starts")
    parser.add_argument(
        "--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR, help="where large_<rows>.csv lives"
    )
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--reps", type=int, default=10, help="repetitions per grid case")
    parser.add_argument(
        "--chart-reps", type=int, default=12, help="requests per aggregate chart case"
    )
    parser.add_argument(
        "--dashboard-rounds", type=int, default=10, help="dashboard refreshes per scenario"
    )
    parser.add_argument(
        "--stat",
        choices=["p50", "p95", "max"],
        default="p95",
        help="statistic compared with the 1 s dashboard budget",
    )
    parser.add_argument("--strict", action="store_true", help="known failures also fail the run")
    parser.add_argument("--json", type=Path, help="write all numbers to this JSON file")
    args = parser.parse_args(argv)
    if args.quick:
        args.rows = 1_000_000
        args.reps = max(args.reps, 10)  # p95 gating needs >= 10
        args.chart_reps = min(args.chart_reps, 8)
        args.dashboard_rounds = min(args.dashboard_rounds, 6)

    csv_path = ensure_fixture(args.rows, args.fixture_dir, args.seed)
    fixture_mib = csv_path.stat().st_size / 2**20
    print(f"bench: {args.rows:,} rows, fixture {csv_path} ({fixture_mib:,.0f} MiB)")
    print(f"env:   {environment()}", flush=True)

    server = Server(args.url, args.port)
    api: Api | None = None
    bench: Bench | None = None
    crashed = ""
    try:
        with timed("server start"):
            server.start()
        api = Api(server.url)
        paths = api.openapi_paths()
        bench = Bench(api, server, args, csv_path)
        steps: list[tuple[str, Callable[[], None]]] = [
            ("import", bench.run_import),
            ("grid pages", bench.run_grid),
            ("aggregated charts", bench.run_chart_aggregates),
            ("payload caps", bench.run_payload_caps),
            ("dashboard refresh, idle", bench.dashboard_idle),
            ("dashboard refresh, during import", bench.dashboard_during_import),
            ("12 concurrent chart queries", bench.twelve_concurrent),
            ("describe", lambda: bench.run_describe(paths)),
        ]
        for label, step in steps:
            print(f"[{label}]", flush=True)
            with timed(label):
                step()
    except Exception as exc:  # report what we have, then fail
        crashed = f"{type(exc).__name__}: {exc}"
        print(f"\nERROR: {crashed}")
        if server.proc is not None:
            print("server log tail:\n" + server.tail())
    finally:
        if api is not None:
            if server.external and bench is not None and bench.dataset_id:
                api.delete_dataset(bench.dataset_id)  # leave an external server as we found it
            api.close()
        server.stop()

    results = bench.results if bench else []
    print()
    print(render_table(results))
    fails = [r for r in results if r.failed and (args.strict or not r.known)]
    known = [r for r in results if r.failed and r.known and not args.strict]
    for r in results:
        if r.note:
            print(f"  - {r.metric}: {r.note}")
    summary = f"{sum(r.status == 'PASS' for r in results)} pass, {len(fails)} fail"
    if known:
        summary += f", {len(known)} known failure(s) not counted"
    summary += f", {sum(r.status == 'N/A' for r in results)} n/a"
    print(summary)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "env": environment(),
            "rows": args.rows,
            "results": [asdict(r) for r in results],
            "error": crashed or None,
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 1 if (fails or crashed) else 0


if __name__ == "__main__":
    sys.exit(main())
