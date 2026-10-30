"""Regenerate the committed sample datasets in samples/ (seeded, deterministic).

    uv run python scripts/gen_samples.py            # rewrite samples/
    uv run python scripts/gen_samples.py --check    # fail if a committed file would change

samples/sales.csv is hand-kept (tests use it) and is never rewritten here. Every other file is
produced from numpy generators seeded per file, so editing one generator never shifts another
file's numbers. Wave 2 plot agents render every Plots.md chart from these files; samples/README.md
says which file feeds which plot. Keep the total under 5 MB.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import xlsxwriter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sample_tables import CITIES, COUNTRIES, US_STATES

SEED = 20261030
REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "samples"
MAX_TOTAL_BYTES = 5 * 1024 * 1024

Row = Sequence[Any]


def rng_for(name: str) -> np.random.Generator:
    """One independent stream per file."""
    return np.random.default_rng([SEED, *name.encode("ascii")])


def fmt(value: Any) -> str:
    """Compact, locale-free cell text. None -> empty (a null in CSV)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.10g}" if abs(value) < 1e15 else f"{value:.0f}"
    return str(value)


def write_csv(
    path: Path,
    header: Sequence[str],
    rows: Iterable[Row],
    *,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> None:
    with path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([fmt(v) for v in row])


def nullify(values: Sequence[Any], rate: float, rng: np.random.Generator) -> list[Any]:
    mask = rng.random(len(values)) < rate
    return [None if m else v for v, m in zip(values, mask, strict=True)]


def zipf_pick(n_items: int, size: int, rng: np.random.Generator, a: float = 1.15) -> np.ndarray:
    """Indices 0..n_items-1 with a long-tailed (Zipf-like) frequency."""
    weights = 1.0 / np.arange(1, n_items + 1) ** a
    return rng.choice(n_items, size=size, p=weights / weights.sum())


# ---------------------------------------------------------------------------------------------
# Business and web data
# ---------------------------------------------------------------------------------------------


def web_traffic(out: Path) -> None:
    """Request log: UTC offsets that differ per row (time-zone-aware timestamps), a Zipf page
    column with ~400 distinct values, bool and nullable revenue."""
    rng = rng_for("web_traffic")
    n = 10_000
    countries = [
        ("United States", -5, 0.30),
        ("India", 5.5, 0.18),
        ("United Kingdom", 0, 0.12),
        ("Germany", 1, 0.10),
        ("Brazil", -3, 0.08),
        ("Japan", 9, 0.07),
        ("Australia", 10, 0.05),
        ("South Africa", 2, 0.04),
        ("Canada", -5, 0.03),
        ("Singapore", 8, 0.03),
    ]
    weights = np.array([c[2] for c in countries])
    country_idx = rng.choice(len(countries), size=n, p=weights / weights.sum())
    start = datetime(2025, 3, 1, tzinfo=UTC)
    # Request times over 45 days, busier in the evening (UTC), with a weekly rhythm.
    day = rng.integers(0, 45, size=n)
    hour = np.clip(rng.normal(15, 5.5, size=n), 0, 23.99)
    seconds = (day * 86400 + hour * 3600).astype(int)
    order = np.argsort(seconds, kind="stable")
    seconds, country_idx = seconds[order], country_idx[order]
    devices = rng.choice(["mobile", "desktop", "tablet"], size=n, p=[0.58, 0.36, 0.06])
    channels = rng.choice(
        ["organic", "paid", "email", "social", "direct", "referral"],
        size=n,
        p=[0.34, 0.2, 0.1, 0.14, 0.15, 0.07],
    )
    page_idx = zipf_pick(400, n, rng)
    sections = ["product", "blog", "docs", "pricing", "help", "category", "cart", "account"]
    load_ms = np.round(rng.lognormal(mean=5.6, sigma=0.55, size=n)).astype(int)
    size_kb = np.round(rng.lognormal(mean=5.0, sigma=0.8, size=n), 1)
    bounced = rng.random(n) < np.where(channels == "paid", 0.55, 0.38)
    revenue = np.where(
        (~bounced) & (rng.random(n) < 0.07), np.round(rng.gamma(2.2, 35, size=n), 2), np.nan
    )
    rows = []
    for i in range(n):
        name, offset_h, _ = countries[int(country_idx[i])]
        instant = start + timedelta(seconds=int(seconds[i]))
        local = instant.astimezone(timezone(timedelta(hours=offset_h)))
        page = f"/{sections[int(page_idx[i]) % len(sections)]}/{int(page_idx[i]) + 1}"
        rows.append(
            (
                local.isoformat(sep=" ", timespec="seconds"),
                name,
                devices[i],
                channels[i],
                page,
                int(load_ms[i]),
                float(size_kb[i]),
                bool(bounced[i]),
                float(revenue[i]),
            )
        )
    write_csv(
        out / "web_traffic.csv",
        [
            "timestamp",
            "country",
            "device",
            "channel",
            "page",
            "load_ms",
            "size_kb",
            "bounced",
            "revenue",
        ],
        rows,
    )


def customers(out: Path) -> None:
    """TSV with high-cardinality categories (company, city), nulls and quoted text."""
    rng = rng_for("customers")
    n = 3_000
    cities = [(name, iso3) for name, iso3, *_ in CITIES]
    # Extend to ~300 city-like labels so `city` is a real high-cardinality column.
    extra = [
        f"{name} {suffix}"
        for name, _ in cities
        for suffix in ("North", "East", "Park", "Bay", "Hill", "Lake")
    ]
    labels = [(c[0], c[1]) for c in cities] + [(e, cities[i // 6][1]) for i, e in enumerate(extra)]
    city_idx = zipf_pick(len(labels), n, rng, a=0.9)
    stems = ["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Vandelay"]
    kinds = ["Labs", "Systems", "Group", "Partners", "Works", "Holdings", "Digital", "Foods"]
    companies = [f"{rng.choice(stems)} {rng.choice(kinds)} {k}" for k in range(1_800)]
    company_idx = zipf_pick(len(companies), n, rng, a=0.7)
    segments = rng.choice(
        ["Consumer", "SMB", "Enterprise", "Public sector"], size=n, p=[0.5, 0.3, 0.15, 0.05]
    )
    base = date(2018, 1, 1)
    signup = [base + timedelta(days=int(d)) for d in np.sort(rng.integers(0, 2500, size=n))]
    age = np.clip(rng.normal(41, 13, size=n), 18, 90).round().astype(int)
    income = np.round(rng.lognormal(mean=10.9, sigma=0.65, size=n), 0)
    credit = np.clip(rng.normal(690, 70, size=n), 300, 850).round().astype(int)
    last = [s + timedelta(days=int(rng.integers(1, 700))) for s in signup]
    satisfaction = np.clip(np.round(rng.normal(3.7, 1.1, size=n)), 1, 5).astype(int)
    note_pool = [
        "Prefers email",
        'Said "call after 5pm"',
        "VIP, renewal due",
        "Billing dispute: see ticket #4821",
        "Referred by a partner; discount applied",
    ]
    notes = [str(rng.choice(note_pool)) if rng.random() < 0.07 else None for _ in range(n)]
    rows = []
    for i in range(n):
        city, iso3 = labels[int(city_idx[i])]
        rows.append(
            (
                f"C{i + 1:06d}",
                companies[int(company_idx[i])],
                city,
                iso3,
                segments[i],
                signup[i].isoformat(),
                None if rng.random() < 0.03 else int(age[i]),
                None if rng.random() < 0.08 else float(income[i]),
                None if rng.random() < 0.05 else int(credit[i]),
                None if rng.random() < 0.15 else min(last[i], date(2025, 12, 31)).isoformat(),
                None if rng.random() < 0.12 else int(satisfaction[i]),
                notes[i],
            )
        )
    write_csv(
        out / "customers.tsv",
        [
            "customer_id",
            "company",
            "city",
            "country_iso3",
            "segment",
            "signup_date",
            "age",
            "income",
            "credit_score",
            "last_purchase",
            "satisfaction",
            "notes",
        ],
        rows,
        delimiter="\t",
    )


def survey_unicode(out: Path) -> None:
    """Unicode column names (CJK, accents, symbols, spaces, parentheses), nulls, UTF-8 with BOM."""
    rng = rng_for("survey_unicode")
    n = 600
    countries = [
        "España",
        "México",
        "日本",
        "中国",
        "Deutschland",
        "France",
        "Brasil",
        "India",
        "Österreich",
        "Türkiye",
    ]
    answers = ["Sí", "No", "À peut-être", "はい", "いいえ", "Vielleicht"]
    names = ["Élodie", "Björn", "李雪", "Zoë", "José", "田中", "Anaïs", "Renée", "Søren", "Łukasz"]
    rows = []
    for i in range(n):
        rows.append(
            (
                i + 1,
                str(rng.choice(names)),
                None if rng.random() < 0.06 else int(np.clip(rng.normal(36, 12), 16, 85)),
                None if rng.random() < 0.18 else round(float(rng.normal(171, 10)), 1),
                None if rng.random() < 0.10 else round(float(rng.gamma(2.0, 1.6)), 1),
                None if rng.random() < 0.12 else round(float(rng.lognormal(3.2, 0.5)), 2),
                str(rng.choice(countries)),
                None if rng.random() < 0.15 else int(rng.integers(1, 6)),
                None if rng.random() < 0.20 else str(rng.choice(answers)),
            )
        )
    header = [
        "id",
        "名前",
        "年齢",
        "Größe (cm)",
        "Café visits / week",
        "prix €",
        "País",
        "Satisfaction ★",
        "Réponse",
    ]
    # utf-8-sig writes the BOM that Excel's "CSV UTF-8" export adds.
    write_csv(out / "survey_unicode.csv", header, rows, encoding="utf-8-sig")


def ventes_cp1252(out: Path) -> None:
    """A French export in Windows-1252 (not UTF-8): accents and the euro sign in the header."""
    rng = rng_for("ventes")
    n = 400
    regions = [
        "Île-de-France",
        "Provence-Alpes-Côte d'Azur",
        "Auvergne-Rhône-Alpes",
        "Occitanie",
        "Bretagne",
        "Hauts-de-France",
    ]
    cities = [
        "Paris",
        "Marseille",
        "Lyon",
        "Toulouse",
        "Rennes",
        "Lille",
        "Bordeaux",
        "Nîmes",
        "Besançon",
        "Créteil",
    ]
    products = [
        "Café moulu",
        "Thé vert",
        "Crème brûlée",
        "Pain d'épices",
        "Châtaignes",
        "Fromage de chèvre",
    ]
    rows = []
    for i in range(n):
        day = date(2024, 1, 1) + timedelta(days=int(rng.integers(0, 365)))
        rows.append(
            (
                f"CMD-{i + 1:04d}",
                day.strftime("%d/%m/%Y"),
                str(rng.choice(regions)),
                str(rng.choice(cities)),
                str(rng.choice(products)),
                int(rng.integers(1, 25)),
                round(float(rng.gamma(3.0, 3.2) + 2.5), 2),
            )
        )
    write_csv(
        out / "ventes_europe_cp1252.csv",
        ["Commande", "Date", "Région", "Ville", "Produit", "Quantité", "Prix (€)"],
        rows,
        encoding="cp1252",
    )


def quarterly_report(out: Path) -> None:
    """Excel workbook with two sheets (Revenue, Headcount) and real Excel dates."""
    rng = rng_for("quarterly_report")
    path = out / "quarterly_report.xlsx"
    book = xlsxwriter.Workbook(str(path), {"default_date_format": "yyyy-mm-dd"})
    book.set_properties(
        {
            "title": "Quarterly report (sample)",
            "author": "Visualize samples",
            "created": datetime(2026, 10, 30),
        }
    )
    money = book.add_format({"num_format": "#,##0.00"})
    date_fmt = book.add_format({"num_format": "yyyy-mm-dd"})
    revenue = book.add_worksheet("Revenue")
    revenue.write_row(0, 0, ["Period", "Region", "Revenue", "Cost", "Margin"])
    regions = ["North", "South", "East", "West", "Central", "Overseas"]
    row = 1
    for year in (2023, 2024):
        for quarter in range(4):
            period = datetime(year, 1 + 3 * quarter, 1)
            for r_i, region in enumerate(regions):
                base = (
                    400_000
                    * (1 + 0.25 * r_i)
                    * (1 + 0.04 * (year - 2023))
                    * (1 + 0.08 * math.sin(quarter))
                )
                rev = float(base * rng.normal(1, 0.06))
                cost = float(rev * rng.uniform(0.55, 0.75))
                revenue.write_datetime(row, 0, period, date_fmt)
                revenue.write(row, 1, region)
                revenue.write_number(row, 2, round(rev, 2), money)
                revenue.write_number(row, 3, round(cost, 2), money)
                revenue.write_formula(
                    row, 4, f"=C{row + 1}-D{row + 1}", money, round(rev - cost, 2)
                )
                row += 1
    revenue.set_column(0, 4, 14)
    headcount = book.add_worksheet("Headcount")
    headcount.write_row(0, 0, ["Department", "Q1", "Q2", "Q3", "Q4"])
    departments = [
        "Engineering",
        "Sales",
        "Support",
        "Marketing",
        "Finance",
        "People",
        "Operations",
        "Legal",
    ]
    sizes = [120, 80, 60, 30, 18, 14, 40, 8]
    for i, (dept, size) in enumerate(zip(departments, sizes, strict=True), start=1):
        headcount.write(i, 0, dept)
        level = size
        for q in range(4):
            level = max(1, level + int(rng.integers(-3, 6)))
            headcount.write_number(i, 1 + q, level)
    headcount.set_column(0, 4, 14)
    book.close()


def products_json(out: Path) -> None:
    """JSON array of records. `dimensions` is a nested object and `tags` a list, so the nested
    JSON path in the import wizard gets exercised."""
    rng = rng_for("products")
    categories = ["Tools", "Garden", "Kitchen", "Toys", "Office", "Outdoor", "Lighting"]
    tags_pool = ["new", "sale", "eco", "bestseller", "limited", "gift", "pro"]
    records = []
    for i in range(120):
        records.append(
            {
                "sku": f"SKU-{1000 + i}",
                "name": f"Product {i + 1}",
                "category": str(rng.choice(categories)),
                "price": round(float(rng.lognormal(3.4, 0.8)), 2),
                "stock": int(rng.integers(0, 500)),
                "rating": None
                if rng.random() < 0.1
                else round(float(np.clip(rng.normal(4.1, 0.6), 1, 5)), 1),
                "discontinued": bool(rng.random() < 0.08),
                "launched": (
                    date(2019, 1, 1) + timedelta(days=int(rng.integers(0, 2400)))
                ).isoformat(),
                "tags": [
                    str(t)
                    for t in rng.choice(tags_pool, size=int(rng.integers(0, 4)), replace=False)
                ],
                "dimensions": {
                    "w": round(float(rng.uniform(5, 80)), 1),
                    "h": round(float(rng.uniform(5, 80)), 1),
                    "d": round(float(rng.uniform(2, 40)), 1),
                },
            }
        )
    # newline="\n": Path.write_text would write CRLF on Windows and make --check platform-dependent.
    (out / "products.json").write_text(
        json.dumps(records, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def events_ndjson(out: Path) -> None:
    """Newline-delimited JSON: one app event per line."""
    rng = rng_for("events")
    n = 2_000
    start = datetime(2025, 6, 1, tzinfo=UTC)
    seconds = np.sort(rng.integers(0, 14 * 86400, size=n))
    kinds = rng.choice(
        ["view", "click", "add_to_cart", "purchase", "search"],
        size=n,
        p=[0.5, 0.25, 0.12, 0.05, 0.08],
    )
    countries = rng.choice(
        ["US", "IN", "GB", "DE", "BR", "JP"], size=n, p=[0.3, 0.2, 0.15, 0.12, 0.12, 0.11]
    )
    lines = []
    for i in range(n):
        value = round(float(rng.gamma(2.0, 30)), 2) if kinds[i] == "purchase" else None
        lines.append(
            json.dumps(
                {
                    "ts": (start + timedelta(seconds=int(seconds[i]))).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "user_id": int(rng.integers(1, 400)),
                    "event": str(kinds[i]),
                    "country": str(countries[i]),
                    "value": value,
                },
                ensure_ascii=False,
            )
        )
    (out / "events.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def sales_hierarchy(out: Path) -> None:
    """Region > category > product revenue for treemap and sunburst (plus a growth measure)."""
    rng = rng_for("sales_hierarchy")
    tree = {
        "Americas": {
            "Software": ["CRM", "Analytics", "Security"],
            "Hardware": ["Laptops", "Phones"],
            "Services": ["Consulting", "Support"],
        },
        "EMEA": {
            "Software": ["CRM", "Analytics"],
            "Hardware": ["Laptops", "Tablets", "Phones"],
            "Services": ["Consulting", "Training"],
        },
        "APAC": {
            "Software": ["Analytics", "Security"],
            "Hardware": ["Phones", "Tablets"],
            "Services": ["Support", "Training"],
        },
    }
    rows = []
    for region, cats in tree.items():
        for category, products in cats.items():
            for product in products:
                rows.append(
                    (
                        region,
                        category,
                        product,
                        round(float(rng.lognormal(5.2, 0.7)) * 10, 1),
                        round(float(rng.normal(0.06, 0.1)), 3),
                    )
                )
    write_csv(
        out / "sales_hierarchy.csv", ["region", "category", "product", "revenue_k", "growth"], rows
    )


def pnl_waterfall(out: Path) -> None:
    rows = [
        (1, "Revenue", 1250.0, "total"),
        (2, "Cost of goods", -480.0, "delta"),
        (3, "Gross profit", 770.0, "total"),
        (4, "Salaries", -310.0, "delta"),
        (5, "Marketing", -95.0, "delta"),
        (6, "R&D", -120.0, "delta"),
        (7, "Other income", 40.0, "delta"),
        (8, "Operating profit", 285.0, "total"),
        (9, "Interest", -22.0, "delta"),
        (10, "Tax", -58.0, "delta"),
        (11, "Net profit", 205.0, "total"),
    ]
    write_csv(out / "pnl_waterfall.csv", ["step", "item", "amount_k", "kind"], rows)


def funnel(out: Path) -> None:
    rows = [
        (1, "Visited site", 48_200),
        (2, "Viewed product", 31_900),
        (3, "Added to cart", 12_700),
        (4, "Started checkout", 8_300),
        (5, "Entered payment", 6_100),
        (6, "Purchased", 5_200),
    ]
    write_csv(out / "funnel.csv", ["stage_order", "stage", "users"], rows)


def defects(out: Path) -> None:
    rows = [
        ("Scratches", 142),
        ("Misalignment", 96),
        ("Paint defects", 61),
        ("Cracks", 38),
        ("Missing parts", 24),
        ("Wrong label", 17),
        ("Packaging damage", 11),
        ("Other", 7),
    ]
    write_csv(out / "defects.csv", ["defect", "count"], rows)


def tasks_gantt(out: Path) -> None:
    rng = rng_for("tasks")
    phases = {
        "Discovery": ["Stakeholder interviews", "Requirements", "Data audit"],
        "Design": ["Architecture", "UX wireframes", "API contract", "Security review"],
        "Build": [
            "Ingest pipeline",
            "Query engine",
            "Chart library",
            "Dashboard layout",
            "Auth",
            "Exports",
        ],
        "Test": ["Unit tests", "Performance tests", "Accessibility audit", "UAT"],
        "Launch": ["Docs", "Training", "Go-live", "Hypercare"],
    }
    owners = ["Priya", "Mateo", "Aiko", "Kwame", "Sofia", "Liam"]
    day = date(2025, 2, 3)
    rows = []
    task_id = 1
    previous_end = day
    for phase, tasks in phases.items():
        phase_start = previous_end
        for task in tasks:
            start = phase_start + timedelta(days=int(rng.integers(0, 6)))
            end = start + timedelta(days=int(rng.integers(4, 16)))
            done = 100 if end < date(2025, 5, 1) else int(rng.integers(0, 80))
            rows.append(
                (
                    task_id,
                    task,
                    phase,
                    str(rng.choice(owners)),
                    start.isoformat(),
                    end.isoformat(),
                    done,
                )
            )
            task_id += 1
            previous_end = max(previous_end, end)
    write_csv(
        out / "tasks_gantt.csv",
        ["task_id", "task", "phase", "owner", "start", "end", "percent_complete"],
        rows,
    )


def flows(out: Path) -> None:
    """Edge list for Sankey/Network: channel -> landing page -> outcome, per quarter."""
    rng = rng_for("flows")
    channels = ["Organic", "Paid search", "Email", "Social", "Referral", "Direct"]
    pages = ["Home", "Pricing", "Blog", "Docs", "Product"]
    outcomes = ["Signup", "Trial", "Purchase", "Bounce"]
    rows = []
    for quarter in ("2024Q1", "2024Q2", "2024Q3", "2024Q4"):
        growth = 1 + 0.12 * (int(quarter[-1]) - 1)
        for ch_i, channel in enumerate(channels):
            for pg_i, page in enumerate(pages):
                value = int(rng.gamma(2.0, 120) * growth * (1.6 - 0.2 * ch_i) * (1.3 - 0.15 * pg_i))
                if value > 40:
                    rows.append((channel, page, value, "channel_to_page", quarter))
        for pg_i, page in enumerate(pages):
            for out_i, outcome in enumerate(outcomes):
                value = int(
                    rng.gamma(2.0, 150) * growth * (1.4 - 0.25 * out_i) * (1.2 - 0.1 * pg_i)
                )
                if value > 40:
                    rows.append((page, outcome, value, "page_to_outcome", quarter))
    write_csv(out / "flows.csv", ["source", "target", "value", "layer", "quarter"], rows)


def journeys(out: Path) -> None:
    """Multi-step paths aggregated to counts: the alluvial plot's input."""
    rng = rng_for("journeys")
    steps = [
        ["Search", "Ad", "Email", "Direct"],
        ["Landing", "Product", "Pricing"],
        ["Cart", "Compare", "Leave"],
        ["Purchased", "Abandoned"],
    ]
    counts: dict[tuple[str, ...], int] = {}
    for _ in range(20_000):
        path = []
        for level, options in enumerate(steps):
            probs = rng.dirichlet(np.ones(len(options)) * (1.5 + level))
            path.append(str(rng.choice(options, p=probs)))
        counts[tuple(path)] = counts.get(tuple(path), 0) + 1
    rows = [(*path, n) for path, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    write_csv(out / "journeys.csv", ["entry", "page", "intent", "outcome", "users"], rows)


# ---------------------------------------------------------------------------------------------
# Time series, finance and econometrics
# ---------------------------------------------------------------------------------------------


def stocks_ohlc(out: Path) -> None:
    """Daily OHLCV for four tickers on business days (open/high/low/close are consistent)."""
    rng = rng_for("stocks")
    tickers = [
        ("NVLX", "Technology", 120.0, 0.0006, 0.021),
        ("BRKS", "Financials", 85.0, 0.0003, 0.012),
        ("ENRG", "Energy", 60.0, 0.0002, 0.017),
        ("CNSM", "Consumer", 45.0, 0.0004, 0.010),
    ]
    days: list[date] = []
    d = date(2023, 1, 2)
    while len(days) < 520:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    rows = []
    for ticker, sector, price0, mu, sigma in tickers:
        price = price0
        for d in days:
            gap = rng.normal(0, sigma * 0.4)
            open_ = price * math.exp(gap)
            ret = rng.normal(mu, sigma)
            close = open_ * math.exp(ret)
            high = max(open_, close) * (1 + abs(rng.normal(0, sigma * 0.5)))
            low = min(open_, close) * (1 - abs(rng.normal(0, sigma * 0.5)))
            volume = int(rng.lognormal(13.5, 0.4) * (1 + 6 * abs(ret)))
            rows.append(
                (
                    d.isoformat(),
                    ticker,
                    sector,
                    round(open_, 2),
                    round(high, 2),
                    round(low, 2),
                    round(close, 2),
                    volume,
                )
            )
            price = close
    write_csv(
        out / "stocks_ohlc.csv",
        ["date", "ticker", "sector", "open", "high", "low", "close", "volume"],
        rows,
    )


def monthly_demand(out: Path) -> None:
    """Monthly series with trend, multiplicative seasonality and noise (ACF, PACF, lag,
    seasonal, moving average)."""
    rng = rng_for("monthly_demand")
    rows = []
    level = 1000.0
    prev_noise = 0.0
    for i in range(168):
        month = date(2010 + i // 12, 1 + i % 12, 1)
        level *= 1.004
        season = (
            1
            + 0.28 * math.sin(2 * math.pi * (month.month - 3) / 12)
            + 0.08 * math.sin(4 * math.pi * month.month / 12)
        )
        prev_noise = 0.55 * prev_noise + rng.normal(0, 0.035)
        promo = 1 if month.month in (11, 12) or rng.random() < 0.1 else 0
        demand = level * season * (1 + prev_noise) * (1 + 0.06 * promo)
        temp = 11 + 12 * math.sin(2 * math.pi * (month.month - 4) / 12) + rng.normal(0, 1.2)
        rows.append((month.isoformat(), round(demand, 1), promo, round(temp, 1)))
    write_csv(out / "monthly_demand.csv", ["month", "demand", "promo", "temp_c"], rows)


def macro_quarterly(out: Path) -> None:
    """Simulated stable VAR(1) for GDP growth, inflation, policy rate, unemployment (IRF)."""
    rng = rng_for("macro")
    a = np.array(
        [
            [0.45, -0.10, -0.12, -0.20],
            [0.08, 0.60, 0.05, -0.05],
            [0.10, 0.25, 0.80, -0.04],
            [-0.12, 0.00, 0.03, 0.85],
        ]
    )
    mean = np.array([2.4, 2.2, 3.0, 6.0])
    shock_sd = np.array([0.7, 0.35, 0.25, 0.18])
    x = mean.copy()
    rows = []
    for i in range(160):
        x = mean + a @ (x - mean) + rng.normal(0, shock_sd)
        quarter = date(1984 + i // 4, 1 + 3 * (i % 4), 1)
        rows.append((quarter.isoformat(), *[round(float(v), 3) for v in x]))
    write_csv(
        out / "macro_quarterly.csv",
        ["quarter", "gdp_growth", "inflation", "policy_rate", "unemployment"],
        rows,
    )


def weather_daily(out: Path) -> None:
    """Three cities x three years of daily weather (calendar heatmap, ridgeline, seasonal)."""
    rng = rng_for("weather")
    cities = [("Oslo", 6.0, 11.0, 0.9), ("Lisbon", 17.5, 6.5, 0.4), ("Singapore", 27.5, 1.0, 1.6)]
    rows = []
    for city, mean_t, amp, wet in cities:
        anomaly = 0.0
        for i in range(1096):
            d = date(2022, 1, 1) + timedelta(days=i)
            doy = d.timetuple().tm_yday
            anomaly = 0.7 * anomaly + rng.normal(0, 1.8)
            temp = mean_t - amp * math.cos(2 * math.pi * (doy - 20) / 365.25) + anomaly
            rain_p = 0.25 * wet * (1.2 - 0.4 * math.cos(2 * math.pi * (doy - 20) / 365.25))
            precip = float(rng.gamma(0.9, 7.0)) if rng.random() < rain_p else 0.0
            humidity = float(
                np.clip(
                    70 + 12 * math.tanh(precip / 8) - 0.6 * (temp - mean_t) + rng.normal(0, 6),
                    25,
                    100,
                )
            )
            wind = float(rng.gamma(4.0, 3.0))
            rows.append(
                (
                    d.isoformat(),
                    city,
                    round(temp, 1),
                    round(precip, 1),
                    round(humidity, 0),
                    round(wind, 1),
                )
            )
    write_csv(
        out / "weather_daily.csv",
        ["date", "city", "temp_c", "precip_mm", "humidity_pct", "wind_kph"],
        rows,
    )


def survival(out: Path) -> None:
    """Right-censored survival times with treatment arms and covariates (Kaplan-Meier)."""
    rng = rng_for("survival")
    n = 600
    treatment = rng.choice(["Control", "Drug A", "Drug B"], size=n, p=[0.4, 0.3, 0.3])
    stage = rng.choice(["I", "II", "III", "IV"], size=n, p=[0.25, 0.35, 0.28, 0.12])
    age = np.clip(rng.normal(62, 11, size=n), 30, 92).round().astype(int)
    sex = rng.choice(["F", "M"], size=n)
    biomarker = rng.normal(0, 1, size=n)
    stage_risk = {"I": 0.0, "II": 0.35, "III": 0.8, "IV": 1.4}
    arm_risk = {"Control": 0.0, "Drug A": -0.45, "Drug B": -0.8}
    linear = (
        np.array([stage_risk[s] + arm_risk[t] for s, t in zip(stage, treatment, strict=True)])
        + 0.02 * (age - 62)
        + 0.25 * biomarker
    )
    scale = 38.0 * np.exp(-linear / 1.3)
    event_time = scale * rng.weibull(1.3, size=n)
    censor_time = rng.uniform(6, 60, size=n)
    time = np.minimum(event_time, censor_time)
    event = (event_time <= censor_time).astype(int)
    rows = [
        (
            f"P{i + 1:04d}",
            round(float(time[i]), 2),
            int(event[i]),
            treatment[i],
            int(age[i]),
            sex[i],
            stage[i],
            round(float(biomarker[i]), 3),
        )
        for i in range(n)
    ]
    write_csv(
        out / "survival.csv",
        ["patient_id", "time_months", "event", "treatment", "age", "sex", "stage", "biomarker"],
        rows,
    )


def panel_event_study(out: Path) -> None:
    """Staggered-adoption panel: 150 units x 20 years with a dynamic treatment effect."""
    rng = rng_for("panel")
    units = 150
    years = list(range(2005, 2025))
    unit_fe = rng.normal(10, 1.2, size=units)
    treat_year = rng.choice([2011, 2013, 2015, 2017, 0, 0], size=units)  # 0 = never treated
    region = rng.choice(["North", "South", "East", "West", "Central"], size=units)
    rows = []
    for u in range(units):
        for year in years:
            t = year - treat_year[u] if treat_year[u] else None
            effect = 0.0
            if t is not None and t >= 0:
                effect = 0.25 + 0.18 * min(t, 6)
            x1 = rng.normal(0, 1)
            outcome = unit_fe[u] + 0.03 * (year - 2005) + 0.4 * x1 + effect + rng.normal(0, 0.35)
            rows.append(
                (
                    f"U{u + 1:03d}",
                    year,
                    round(float(outcome), 4),
                    int(treat_year[u] > 0),
                    int(treat_year[u]) if treat_year[u] else None,
                    t,
                    int(t is not None and t >= 0),
                    round(float(x1), 3),
                    region[u],
                )
            )
    write_csv(
        out / "panel_event_study.csv",
        [
            "unit",
            "year",
            "outcome",
            "ever_treated",
            "treat_year",
            "event_time",
            "post",
            "x1",
            "region",
        ],
        rows,
    )


def churn(out: Path) -> None:
    """Binary outcome with covariates and two model scores (ROC, PR, marginal effects)."""
    rng = rng_for("churn")
    n = 2_000
    tenure = np.clip(rng.gamma(2.2, 14, size=n), 1, 72).round().astype(int)
    charges = np.round(np.clip(rng.normal(68, 24, size=n), 18, 140), 2)
    calls = rng.poisson(1.6, size=n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], size=n, p=[0.55, 0.25, 0.2])
    autopay = rng.random(n) < 0.45
    age = np.clip(rng.normal(44, 15, size=n), 18, 85).round().astype(int)
    contract_effect = np.where(
        contract == "Month-to-month", 0.9, np.where(contract == "One year", -0.2, -0.9)
    )
    logit = (
        -0.6
        + contract_effect
        - 0.035 * tenure
        + 0.014 * (charges - 68)
        + 0.28 * calls
        - 0.35 * autopay
        - 0.008 * (age - 44)
    )
    true_p = 1 / (1 + np.exp(-logit))
    churned = (rng.random(n) < true_p).astype(int)
    score_a = np.clip(true_p + rng.normal(0, 0.12, size=n), 0.001, 0.999)
    score_b = np.clip(0.5 * true_p + 0.5 * rng.random(n) + rng.normal(0, 0.1, size=n), 0.001, 0.999)
    rows = [
        (
            f"K{i + 1:05d}",
            int(tenure[i]),
            float(charges[i]),
            int(calls[i]),
            contract[i],
            bool(autopay[i]),
            int(age[i]),
            int(churned[i]),
            round(float(score_a[i]), 4),
            round(float(score_b[i]), 4),
        )
        for i in range(n)
    ]
    write_csv(
        out / "churn.csv",
        [
            "customer_id",
            "tenure_months",
            "monthly_charges",
            "support_calls",
            "contract",
            "autopay",
            "age",
            "churned",
            "score_model_a",
            "score_model_b",
        ],
        rows,
    )


def studies(out: Path) -> None:
    """Meta-analysis table: effect sizes with confidence intervals (forest plot)."""
    rng = rng_for("studies")
    rows = []
    for i in range(16):
        n = int(rng.integers(40, 600))
        effect = float(rng.normal(0.32, 0.18))
        se = float(1.6 / math.sqrt(n))
        rows.append(
            (
                f"Study {chr(65 + i)}",
                2008 + i,
                n,
                round(effect, 3),
                round(effect - 1.96 * se, 3),
                round(effect + 1.96 * se, 3),
                round(1 / se**2, 1),
                "RCT" if i % 3 else "Observational",
            )
        )
    write_csv(
        out / "studies.csv",
        ["study", "year", "n", "effect", "ci_low", "ci_high", "weight", "design"],
        rows,
    )


# ---------------------------------------------------------------------------------------------
# Multivariate and relationship data
# ---------------------------------------------------------------------------------------------


def specimens(out: Path) -> None:
    """Three species x four correlated measurements, nulls, plus skewed and heavy-tailed columns."""
    rng = rng_for("specimens")
    species = {
        "Adelie": (38.8, 18.3, 190.0, 3700.0, 0.0),
        "Chinstrap": (48.8, 18.4, 195.8, 3733.0, 0.5),
        "Gentoo": (47.5, 15.0, 217.2, 5076.0, 1.0),
    }
    islands = {
        "Adelie": ["Torgersen", "Biscoe", "Dream"],
        "Chinstrap": ["Dream"],
        "Gentoo": ["Biscoe"],
    }
    rows = []
    idx = 1
    for name, (bill_l, bill_d, flipper, mass, _) in species.items():
        for _ in range(150):
            sex = "female" if rng.random() < 0.5 else "male"
            shift = 0.9 if sex == "male" else -0.9
            size = rng.normal(0, 1)
            values = [
                bill_l + 3.0 * size + shift * 1.2 + rng.normal(0, 1.4),
                bill_d + 0.9 * size + shift * 0.5 + rng.normal(0, 0.8),
                flipper + 6.0 * size + shift * 2.0 + rng.normal(0, 3.0),
                mass + 330 * size + shift * 260 + rng.normal(0, 200),
            ]
            rows.append(
                (
                    f"S{idx:04d}",
                    name,
                    str(rng.choice(islands[name])),
                    None if rng.random() < 0.03 else sex,
                    *[None if rng.random() < 0.02 else round(float(v), 1) for v in values],
                    round(float(rng.lognormal(1.9, 0.6)), 2),
                    round(float(rng.standard_t(3) * 1.5), 3),
                )
            )
            idx += 1
    order = rng.permutation(len(rows))
    rows = [rows[int(i)] for i in order]
    write_csv(
        out / "specimens.csv",
        [
            "specimen_id",
            "species",
            "island",
            "sex",
            "bill_length_mm",
            "bill_depth_mm",
            "flipper_length_mm",
            "body_mass_g",
            "recovery_days",
            "measurement_error",
        ],
        rows,
    )


def points_2d(out: Path) -> None:
    """Gaussian mixture in the plane with heteroscedastic noise (hexbin, contour, scatter,
    bubble, regression and residual plots)."""
    rng = rng_for("points_2d")
    n = 6_000
    centers = [(-2.0, -1.0, 1.0, 0.7), (1.5, 2.0, 0.8, 1.1), (3.5, -2.0, 0.6, 0.6)]
    probs = [0.45, 0.35, 0.20]
    group = rng.choice(3, size=n, p=probs)
    x = np.array([rng.normal(centers[g][0], centers[g][2]) for g in group])
    y_lin = 1.5 + 0.9 * x + rng.normal(0, 0.4 + 0.15 * np.abs(x))
    y_mix = np.array([rng.normal(centers[g][1], centers[g][3]) for g in group])
    weight = np.round(rng.gamma(2.0, 1.5, size=n), 2)
    rows = [
        (
            round(float(x[i]), 3),
            round(float(y_lin[i]), 3),
            round(float(y_mix[i]), 3),
            "ABC"[int(group[i])],
            float(weight[i]),
        )
        for i in range(n)
    ]
    write_csv(out / "points_2d.csv", ["x", "y_linear", "y_cluster", "group", "weight"], rows)


# ---------------------------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------------------------


def countries(out: Path) -> None:
    """One row per country, keyed by ISO 3166-1 alpha-3 (the Natural Earth join key)."""
    rng = rng_for("countries")
    rows = []
    for iso3, name, continent, lat, lon, pop_m, gdp in COUNTRIES:
        log_gdp = math.log10(gdp)
        life_2020 = float(np.clip(40 + 9.3 * log_gdp + rng.normal(0, 2.0), 50, 84.5))
        gain = max(0.5, float(rng.normal(8.0 - 1.2 * (log_gdp - 3), 2.0)))
        life_2000 = life_2020 - gain
        co2 = max(0.05, 0.9 * (gdp / 1000) ** 0.62 * float(rng.lognormal(0, 0.45)))
        internet = float(
            np.clip(100 / (1 + math.exp(-(log_gdp - 3.6) * 2.6)) + rng.normal(0, 4), 3, 99)
        )
        rows.append(
            (
                iso3,
                name,
                continent,
                lat,
                lon,
                pop_m,
                gdp,
                round(life_2000, 1),
                round(life_2020, 1),
                round(co2, 2),
                round(internet, 1),
            )
        )
    write_csv(
        out / "countries.csv",
        [
            "iso3",
            "country",
            "continent",
            "lat",
            "lon",
            "population_m",
            "gdp_per_capita",
            "life_exp_2000",
            "life_exp_2020",
            "co2_per_capita",
            "internet_pct",
        ],
        rows,
    )


def us_states(out: Path) -> None:
    rng = rng_for("us_states")
    rows = [
        (
            name,
            code,
            region,
            pop,
            income,
            round(float(np.clip(rng.normal(3.9, 0.9), 2.0, 6.5)), 1),
            round(float(np.clip(rng.normal(86, 7), 65, 100)), 1),
        )
        for name, code, region, pop, income in US_STATES
    ]
    write_csv(
        out / "us_states.csv",
        [
            "state",
            "usps",
            "region",
            "population_m",
            "median_income_k",
            "unemployment_pct",
            "broadband_pct",
        ],
        rows,
    )


def cities(out: Path) -> None:
    rows = [(name, iso3, lat, lon, pop) for name, iso3, lat, lon, pop in CITIES]
    write_csv(out / "cities.csv", ["city", "iso3", "lat", "lon", "population_m"], rows)


def geo_events(out: Path) -> None:
    """Clustered point events around real cities plus scattered background noise."""
    rng = rng_for("geo_events")
    n = 4_000
    hubs = [(c[2], c[3], c[4]) for c in CITIES[:24]]
    weights = np.array([h[2] for h in hubs])
    which = rng.choice(len(hubs), size=n, p=weights / weights.sum())
    noise = rng.random(n) < 0.12
    lat = np.where(
        noise,
        rng.uniform(-55, 70, size=n),
        [hubs[i][0] for i in which] + rng.normal(0, 1.1, size=n),
    )
    lon = np.where(
        noise,
        rng.uniform(-170, 175, size=n),
        [hubs[i][1] for i in which] + rng.normal(0, 1.4, size=n),
    )
    magnitude = np.round(2.0 + rng.exponential(0.8, size=n), 1)
    depth = np.round(rng.gamma(2.0, 18.0, size=n), 1)
    category = rng.choice(
        ["minor", "light", "moderate", "strong"], size=n, p=[0.55, 0.3, 0.12, 0.03]
    )
    start = datetime(2025, 1, 1)
    times = np.sort(rng.integers(0, 365 * 86400, size=n))
    rows = [
        (
            round(float(np.clip(lat[i], -89, 89)), 4),
            round(float(((lon[i] + 180) % 360) - 180), 4),
            float(magnitude[i]),
            float(depth[i]),
            category[i],
            (start + timedelta(seconds=int(times[i]))).isoformat(sep=" "),
        )
        for i in range(n)
    ]
    write_csv(
        out / "geo_events.csv",
        ["lat", "lon", "magnitude", "depth_km", "category", "event_time"],
        rows,
    )


GENERATORS = [
    web_traffic,
    customers,
    survey_unicode,
    ventes_cp1252,
    quarterly_report,
    products_json,
    events_ndjson,
    sales_hierarchy,
    pnl_waterfall,
    funnel,
    defects,
    tasks_gantt,
    flows,
    journeys,
    stocks_ohlc,
    monthly_demand,
    macro_quarterly,
    weather_daily,
    survival,
    panel_event_study,
    churn,
    studies,
    specimens,
    points_2d,
    countries,
    us_states,
    cities,
    geo_events,
]


def generate(target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in target.iterdir()}
    for generator in GENERATORS:
        generator(target)
    produced = sorted(p for p in target.iterdir() if p.name not in before)
    return produced


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=SAMPLES_DIR, help="output directory")
    parser.add_argument("--check", action="store_true", help="fail if committed samples differ")
    args = parser.parse_args()

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            produced = generate(Path(tmp))
            drift = [
                p.name
                for p in produced
                if not (args.out / p.name).is_file()
                or (args.out / p.name).read_bytes() != p.read_bytes()
            ]
        if drift:
            print("samples out of date: " + ", ".join(drift))
            return 1
        print("samples up to date")
        return 0

    produced = generate(args.out)
    total = sum(p.stat().st_size for p in args.out.iterdir() if p.is_file())
    for p in produced:
        print(f"{p.name:32s} {p.stat().st_size / 1024:9.1f} KiB")
    mib = 1024 * 1024
    print(
        f"total (incl. existing files): {total / mib:.2f} MiB (limit {MAX_TOTAL_BYTES // mib} MiB)"
    )
    return 0 if total <= MAX_TOTAL_BYTES else 1


if __name__ == "__main__":
    raise SystemExit(main())
