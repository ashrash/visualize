"""Seed a "Demo" project with the datasets from gen_demo_data.py and a chart for each.

Requires a running server (`uv run viz serve` or `uv run viz dev`) and the files from
`uv run python scripts/gen_demo_data.py`. Talks to the real HTTP API only (import-from-path,
project save) so the result is exactly what a user would get by clicking through the app -
nothing here bypasses the normal import/chart/dashboard flow.

    uv run python scripts/seed_demo_project.py                        # http://127.0.0.1:8000
    uv run python scripts/seed_demo_project.py --base-url http://127.0.0.1:5173

Re-running reuses datasets already imported under the same name, and replaces the "Demo"
project if one already exists (matched by name) rather than creating a duplicate.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "demo"
PROJECT_NAME = "Demo"


def new_id() -> str:
    return str(uuid.uuid4())


def import_dataset(client: httpx.Client, existing: dict[str, Any], name: str, path: Path) -> dict:
    if name in existing:
        print(f"dataset {name!r}: reusing {existing[name]['id']}")
        return existing[name]
    resp = client.post("/datasets/import-path", json={"path": str(path), "name": name})
    resp.raise_for_status()
    dataset = resp.json()
    print(f"dataset {name!r}: imported {dataset['id']} ({dataset['versions'][-1]['rowCount']:,} rows)")
    return dataset


def enc(field: str | None = None, agg: str | None = None, time_unit: str | None = None) -> dict:
    out: dict[str, Any] = {}
    if field is not None:
        out["field"] = field
    if agg is not None:
        out["agg"] = agg
    if time_unit is not None:
        out["timeUnit"] = time_unit
    return out


def chart(
    name: str,
    plot_type: str,
    dataset_id: str,
    encodings: dict[str, list[dict]],
    params: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
) -> dict:
    return {
        "id": new_id(),
        "name": name,
        "plotType": plot_type,
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": encodings,
        "params": params or {},
        "style": style or {},
        "interactions": {"zoom": True, "brush": False, "clickFilter": True, "drillHierarchy": []},
        "filters": [],
    }


def layout_tab(tab_id: str, name: str, charts: list[dict]) -> dict:
    """Two charts per row, 6 wide x 8 tall each, in the order given."""
    items = []
    for idx, c in enumerate(charts):
        items.append(
            {
                "i": new_id(),
                "x": (idx % 2) * 6,
                "y": (idx // 2) * 8,
                "w": 6,
                "h": 8,
                "widget": {"kind": "chart", "chartId": c["id"]},
            }
        )
    return {"id": tab_id, "name": name, "items": items}


def build_charts(datasets: dict[str, dict]) -> dict[str, list[dict]]:
    orders = datasets["orders"]["id"]
    measurements = datasets["measurements"]["id"]
    web = datasets["web_traffic_large"]["id"]
    geo = datasets["geo_events_large"]["id"]
    churn = datasets["churn_large"]["id"]

    by_tab: dict[str, list[dict]] = {}

    by_tab["Orders"] = [
        chart(
            "Sales by Region",
            "bar",
            orders,
            {"x": [enc("region")], "y": [enc("sales", "sum")]},
        ),
        chart(
            "Sales by Region and Channel",
            "stacked_bar",
            orders,
            {"x": [enc("region")], "y": [enc("sales", "sum")], "color": [enc("channel")]},
            style={"stack": "stack"},
        ),
        chart(
            "Sales Share by Channel",
            "pie",
            orders,
            {"x": [enc("channel")], "y": [enc("sales", "sum")]},
        ),
        chart(
            "Monthly Sales Trend",
            "timeseries_line",
            orders,
            {"x": [enc("order_date", time_unit="month")], "y": [enc("sales", "sum")]},
        ),
        chart(
            "Revenue by Category and Product",
            "treemap",
            orders,
            {"label": [enc("category"), enc("product")], "size": [enc("sales", "sum")]},
        ),
    ]

    by_tab["Distributions"] = [
        chart(
            "x1 Distribution by Group",
            "histogram",
            measurements,
            {"x": [enc("x1")], "color": [enc("group")]},
        ),
        chart("x2 by Group", "box", measurements, {"y": [enc("x2")], "x": [enc("group")]}),
        chart(
            "x1 Distribution by Group (Violin)",
            "violin",
            measurements,
            {"y": [enc("x1")], "x": [enc("group")]},
        ),
        chart(
            "x1 vs x2 by Group",
            "scatter",
            measurements,
            {"x": [enc("x1")], "y": [enc("x2")], "color": [enc("group")]},
        ),
        chart(
            "Correlation Matrix",
            "correlation_heatmap",
            measurements,
            {"dimensions": [enc(f) for f in ["x1", "x2", "x3", "x4", "x5", "x6"]]},
        ),
    ]

    by_tab["Web Traffic"] = [
        chart(
            "Daily Sessions by Device",
            "timeseries_line",
            web,
            {"x": [enc("timestamp", time_unit="day")], "y": [], "color": [enc("device")]},
        ),
        chart(
            "Daily Revenue Calendar",
            "calendar_heatmap",
            web,
            {"x": [enc("timestamp")], "y": [enc("revenue", "sum")]},
        ),
        chart(
            "Load Time by Device",
            "box",
            web,
            {"y": [enc("load_ms")], "x": [enc("device")]},
        ),
    ]

    by_tab["Geo"] = [
        chart(
            "Events by Location",
            "bubble_map",
            geo,
            {
                "lat": [enc("lat")],
                "lon": [enc("lon")],
                "size": [enc("value", "sum")],
                "color": [enc("category")],
            },
        ),
        chart(
            "Event Density Heatmap",
            "geo_heatmap",
            geo,
            {"lat": [enc("lat")], "lon": [enc("lon")], "value": [enc("value", "sum")]},
        ),
        chart(
            "Value by Country",
            "choropleth",
            geo,
            {"region": [enc("country")], "value": [enc("value", "sum")]},
        ),
    ]

    by_tab["Churn"] = [
        chart(
            "ROC Curve: Model A vs Model B",
            "roc",
            churn,
            {"truth": [enc("churned")], "score": [enc("score_model_a"), enc("score_model_b")]},
        ),
        chart(
            "Precision-Recall: Model A vs Model B",
            "precision_recall",
            churn,
            {"truth": [enc("churned")], "score": [enc("score_model_a"), enc("score_model_b")]},
        ),
        chart(
            "Monthly Charges Distribution",
            "histogram",
            churn,
            {"x": [enc("monthly_charges")], "color": [enc("contract")]},
        ),
    ]

    return by_tab


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="server base URL")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="dir with the demo CSVs")
    args = parser.parse_args()

    files = {
        "orders": args.data_dir / "orders.csv",
        "measurements": args.data_dir / "measurements.csv",
        "web_traffic_large": args.data_dir / "web_traffic_large.csv",
        "geo_events_large": args.data_dir / "geo_events_large.csv",
        "churn_large": args.data_dir / "churn_large.csv",
    }
    missing = [p for p in files.values() if not p.is_file()]
    if missing:
        print(f"missing demo data files: {missing}; run scripts/gen_demo_data.py first", file=sys.stderr)
        return 1

    with httpx.Client(base_url=f"{args.base_url}/api", timeout=120.0) as client:
        resp = client.get("/datasets")
        resp.raise_for_status()
        existing = {d["name"]: d for d in resp.json()}

        datasets = {
            name: import_dataset(client, existing, name, path) for name, path in files.items()
        }

        resp = client.get("/projects")
        resp.raise_for_status()
        existing_project = next((p for p in resp.json() if p["name"] == PROJECT_NAME), None)
        if existing_project is not None:
            resp = client.get(f"/projects/{existing_project['id']}")
            resp.raise_for_status()
            project = resp.json()
            print(f"project {PROJECT_NAME!r}: reusing {project['id']} (rev {project['revision']})")
        else:
            resp = client.post("/projects", json={"name": PROJECT_NAME})
            resp.raise_for_status()
            project = resp.json()
            print(f"project {PROJECT_NAME!r}: created {project['id']}")

        by_tab = build_charts(datasets)
        all_charts = [c for charts in by_tab.values() for c in charts]
        tabs = [layout_tab(new_id(), tab_name, charts) for tab_name, charts in by_tab.items()]

        project["datasetIds"] = sorted({d["id"] for d in datasets.values()})
        project["charts"] = all_charts
        project["dashboards"] = [
            {
                "id": new_id(),
                "name": "Demo dashboard",
                "tabs": tabs,
                "filters": [],
                "linkGroups": [],
                "themeId": None,
            }
        ]

        resp = client.put(f"/projects/{project['id']}", json=project)
        resp.raise_for_status()
        saved = resp.json()
        print(
            f"project {PROJECT_NAME!r}: saved rev {saved['revision']} with "
            f"{len(saved['charts'])} charts across {len(tabs)} dashboard tabs"
        )
        print(f"open: {args.base_url}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
