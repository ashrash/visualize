"""Generate seeded demo datasets (>= 10,000 rows each) for a live demo of the app.

    uv run python scripts/gen_demo_data.py              # write data/demo/*.csv
    uv run python scripts/gen_demo_data.py --force       # regenerate even if files exist

Unlike samples/ (small, 5 MB-capped CI fixtures), these are meant to be imported into a real,
running `viz serve`/`viz dev` instance to populate a demo project with full-size charts. Output
goes to the git-ignored data/demo/ (same convention as data/perf/ from gen_large_dataset.py).
Each file has its own seeded DuckDB hash stream, so it is reproducible and independent of the
others. See scripts/seed_demo_project.py for the matching chart/dashboard builder.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sample_tables import COUNTRIES

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "demo"
SEED = 20261031


def _connect(out_dir: Path, seed_offset: int) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{(out_dir / 'tmp').as_posix()}'")
    con.execute(
        "CREATE MACRO u(i, k) AS "
        f"(hash(i * 31 + k * 1000003 + {SEED + seed_offset}) >> 11)::DOUBLE / 9007199254740992.0"
    )
    return con


def _write(con: duckdb.DuckDBPyConnection, query: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    con.execute(f"COPY ({query}) TO '{tmp.as_posix()}' (FORMAT csv, HEADER)")
    tmp.replace(path)
    (count,) = con.execute(f"SELECT count(*) FROM read_csv_auto('{path.as_posix()}')").fetchone()
    return int(count)


# -- 1. orders: basic / composition / time series -------------------------------------------

ORDERS_ROWS = 15_000


def orders_query(rows: int) -> str:
    return f"""
    WITH t AS (
      SELECT i,
        u(i, 1) AS u_ts, u(i, 2) AS u_region, u(i, 3) AS u_cat, u(i, 4) AS u_prod,
        u(i, 5) AS u_chan, u(i, 6) AS u_seg, u(i, 7) AS u_cust, u(i, 8) AS u_units,
        u(i, 9) AS u_p1, u(i, 10) AS u_p2, u(i, 11) AS u_disc_on, u(i, 12) AS u_disc,
        u(i, 13) AS u_ret
      FROM range({rows}) AS r(i)
    ), c AS (
      SELECT *,
        1 + least(49, floor(-ln(1 - u_units) * 4))::INTEGER AS units_v,
        round(exp(2.2 + 0.7 * sqrt(-2 * ln(1 - u_p1)) * cos(2 * pi() * u_p2)), 2) AS price_v,
        CASE WHEN u_disc_on < 0.3
             THEN list_extract([0.05, 0.1, 0.15, 0.2]::DOUBLE[], 1 + floor(u_disc * 4)::INTEGER)
        END AS discount_v
      FROM t
    )
    SELECT
      (i + 1)::BIGINT AS order_id,
      TIMESTAMP '2024-01-01' + to_seconds(floor(u_ts * 730 * 86400)::BIGINT) AS order_ts,
      CAST(TIMESTAMP '2024-01-01' + to_seconds(floor(u_ts * 730 * 86400)::BIGINT) AS DATE)
        AS order_date,
      list_extract(['North', 'South', 'East', 'West', 'Central'],
                   1 + floor(u_region * 5)::INTEGER) AS region,
      list_extract(['Electronics', 'Home', 'Apparel', 'Sporting Goods', 'Grocery', 'Toys'],
                   1 + floor(pow(u_cat, 1.3) * 6)::INTEGER) AS category,
      'Product ' || lpad((floor(pow(u_prod, 1.4) * 120)::INTEGER + 1)::VARCHAR, 3, '0')
        AS product,
      list_extract(['Retail', 'Online', 'Wholesale', 'Partner'], 1 + floor(u_chan * 4)::INTEGER)
        AS channel,
      list_extract(['Consumer', 'SMB', 'Enterprise'], 1 + floor(u_seg * 3)::INTEGER) AS segment,
      'C' || lpad((floor(pow(u_cust, 1.3) * 2500)::BIGINT + 1)::VARCHAR, 6, '0') AS customer_id,
      units_v AS units,
      price_v AS unit_price,
      discount_v AS discount,
      round(units_v * price_v * (1 - coalesce(discount_v, 0)), 2) AS sales,
      u_ret < 0.05 AS is_returned
    FROM c
    """


# -- 2. measurements: distribution / relationship / multivariate ----------------------------

MEASUREMENTS_ROWS = 12_000


def measurements_query(rows: int) -> str:
    return f"""
    WITH t AS (
      SELECT i,
        u(i, 1) AS u_grp,
        u(i, 2) AS u_z1, u(i, 3) AS u_z2,
        u(i, 4) AS u_n1, u(i, 5) AS u_n2, u(i, 6) AS u_n3, u(i, 7) AS u_n4,
        u(i, 8) AS u_n5, u(i, 9) AS u_n6, u(i, 10) AS u_skew1, u(i, 11) AS u_skew2
      FROM range({rows}) AS r(i)
    ), g AS (
      SELECT *,
        1 + floor(u_grp * 5)::INTEGER AS grp_idx,
        sqrt(-2 * ln(1 - u_z1)) * cos(2 * pi() * u_z2) AS z
      FROM t
    )
    SELECT
      (i + 1)::BIGINT AS id,
      list_extract(['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'], grp_idx) AS "group",
      round((grp_idx - 3) * 1.5 + z + sqrt(-2 * ln(1 - u_n1)) * cos(2 * pi() * u_n2) * 0.6, 3)
        AS x1,
      round((grp_idx - 3) * 0.8 + 0.7 * z
            + sqrt(-2 * ln(1 - u_n2)) * cos(2 * pi() * u_n3) * 0.8, 3) AS x2,
      round(-0.5 * z + sqrt(-2 * ln(1 - u_n3)) * cos(2 * pi() * u_n4) * 1.1, 3) AS x3,
      round(sqrt(-2 * ln(1 - u_n4)) * cos(2 * pi() * u_n5) * 1.5, 3) AS x4,
      round((grp_idx - 3) * 1.1 + sqrt(-2 * ln(1 - u_n5)) * cos(2 * pi() * u_n6) * 0.9, 3) AS x5,
      round(exp(0.5 + sqrt(-2 * ln(1 - u_skew1)) * cos(2 * pi() * u_skew2) * 0.5), 3) AS x6,
      round(50 + 10 * z + (grp_idx - 3) * 4, 2) AS score
    FROM g
    """


# -- 3. web_traffic_large: time series --------------------------------------------------------

WEB_TRAFFIC_ROWS = 20_000


def web_traffic_query(rows: int) -> str:
    return f"""
    WITH t AS (
      SELECT i,
        u(i, 1) AS u_day, u(i, 2) AS u_tod, u(i, 3) AS u_country, u(i, 4) AS u_device,
        u(i, 5) AS u_chan, u(i, 6) AS u_page, u(i, 7) AS u_l1, u(i, 8) AS u_l2,
        u(i, 9) AS u_bounce, u(i, 10) AS u_rev_on, u(i, 11) AS u_rev
      FROM range({rows}) AS r(i)
    ), d AS (
      SELECT *,
        floor(pow(u_day, 1.0) * 180)::INTEGER AS day_idx,
        (0.5 + 0.5 * sin((pow(u_day, 1.0) * 180) / 7.0 * 2 * pi())) AS weekly_wave
      FROM t
    )
    SELECT
      TIMESTAMP '2026-05-01' + to_days(day_idx) + to_seconds(floor(u_tod * 86400)::BIGINT)
        AS "timestamp",
      list_extract(['US', 'GB', 'DE', 'IN', 'BR', 'JP', 'FR', 'CA', 'AU', 'MX'],
                   1 + floor(pow(u_country, 1.5) * 10)::INTEGER) AS country,
      list_extract(['desktop', 'mobile', 'tablet'], 1 + floor(u_device * 3)::INTEGER) AS device,
      list_extract(['Organic', 'Paid', 'Referral', 'Direct', 'Social'],
                   1 + floor(u_chan * 5)::INTEGER) AS channel,
      'Page ' || lpad((floor(pow(u_page, 2.0) * 150)::INTEGER + 1)::VARCHAR, 3, '0') AS page,
      round(exp(4.5 + sqrt(-2 * ln(1 - u_l1)) * cos(2 * pi() * u_l2) * 0.5), 1) AS load_ms,
      u_bounce < (0.25 + 0.1 * weekly_wave) AS bounced,
      CASE WHEN u_rev_on < 0.08 THEN round(exp(2 + u_rev * 3), 2) END AS revenue
    FROM d
    """


# -- 4. geo_events: geospatial ----------------------------------------------------------------

GEO_EVENTS_ROWS = 10_000


def geo_events_query(rows: int) -> str:
    country_count = len(COUNTRIES)
    values = ", ".join(
        f"('{iso3}', '{name.replace(chr(39), chr(39) * 2)}', '{continent}', {lat}, {lon}, {pop})"
        for iso3, name, continent, lat, lon, pop, _gdp in COUNTRIES
    )
    return f"""
    WITH countries(iso3, country, continent, c_lat, c_lon, pop_m) AS (VALUES {values}),
    t AS (
      SELECT i,
        u(i, 1) AS u_country, u(i, 2) AS u_lat, u(i, 3) AS u_lon, u(i, 4) AS u_cat,
        u(i, 5) AS u_val1, u(i, 6) AS u_val2, u(i, 7) AS u_day
      FROM range({rows}) AS r(i)
    ), ranked AS (
      SELECT *, row_number() OVER (ORDER BY pop_m DESC) AS rnk
      FROM countries
    )
    SELECT
      (i + 1)::BIGINT AS event_id,
      TIMESTAMP '2026-01-01' + to_days(floor(u_day * 300)::INTEGER) AS "timestamp",
      ranked.iso3,
      ranked.country,
      ranked.continent AS region,
      round(ranked.c_lat + (u_lat - 0.5) * 6, 4) AS lat,
      round(ranked.c_lon + (u_lon - 0.5) * 6, 4) AS lon,
      list_extract(['Shipment', 'Incident', 'Signup', 'Inspection', 'Outage'],
                   1 + floor(u_cat * 5)::INTEGER) AS category,
      round(exp(1.5 + sqrt(-2 * ln(1 - u_val1)) * cos(2 * pi() * u_val2) * 0.7), 2) AS value
    FROM t
    JOIN ranked ON ranked.rnk = 1 + floor(pow(u_country, 1.7) * {country_count})::INTEGER
    """


# -- 5. churn_large: econometric / relationship ------------------------------------------------

CHURN_ROWS = 10_000


def churn_query(rows: int) -> str:
    return f"""
    WITH t AS (
      SELECT i,
        u(i, 1) AS u_tenure, u(i, 2) AS u_charge1, u(i, 3) AS u_charge2, u(i, 4) AS u_calls,
        u(i, 5) AS u_contract, u(i, 6) AS u_autopay, u(i, 7) AS u_age, u(i, 8) AS u_noise,
        u(i, 9) AS u_na1, u(i, 10) AS u_na2, u(i, 11) AS u_nb1, u(i, 12) AS u_nb2
      FROM range({rows}) AS r(i)
    ), c AS (
      SELECT *,
        1 + floor(-ln(1 - u_tenure) * 20)::INTEGER AS tenure_v,
        round(30 + exp(sqrt(-2 * ln(1 - u_charge1)) * cos(2 * pi() * u_charge2) * 0.3) * 20, 2)
          AS charge_v,
        least(10, floor(-ln(1 - u_calls) * 2))::INTEGER AS calls_v,
        list_extract(['Month-to-month', 'One year', 'Two year'],
                     1 + floor(pow(u_contract, 1.3) * 3)::INTEGER) AS contract_v,
        u_autopay < 0.55 AS autopay_v,
        18 + floor(u_age * 60)::INTEGER AS age_v
      FROM t
    ), risk AS (
      SELECT *,
        (1.0 / 60 * (60 - least(tenure_v, 60))
         + 0.15 * calls_v
         + CASE contract_v WHEN 'Month-to-month' THEN 0.5 WHEN 'One year' THEN 0.15 ELSE 0 END
         + CASE WHEN autopay_v THEN -0.2 ELSE 0.1 END
         + (charge_v - 50) / 200.0) AS risk_score
      FROM c
    )
    SELECT
      'C' || lpad((i + 1)::VARCHAR, 6, '0') AS customer_id,
      tenure_v AS tenure_months,
      charge_v AS monthly_charges,
      calls_v AS support_calls,
      contract_v AS contract,
      autopay_v AS autopay,
      age_v AS age,
      (u_noise < (1.0 / (1.0 + exp(-(risk_score - 1.0) * 2.5))))::INTEGER AS churned,
      least(0.99, greatest(0.01,
        1.0 / (1.0 + exp(-(risk_score - 1.0) * 2.5))
        + (sqrt(-2 * ln(1 - u_na1)) * cos(2 * pi() * u_na2)) * 0.05)) AS score_model_a,
      least(0.99, greatest(0.01,
        1.0 / (1.0 + exp(-(risk_score - 1.0) * 1.1))
        + (sqrt(-2 * ln(1 - u_nb1)) * cos(2 * pi() * u_nb2)) * 0.18)) AS score_model_b
    FROM risk
    """


DATASETS: dict[str, tuple[int, int]] = {
    "orders": (ORDERS_ROWS, 1),
    "measurements": (MEASUREMENTS_ROWS, 2),
    "web_traffic_large": (WEB_TRAFFIC_ROWS, 3),
    "geo_events_large": (GEO_EVENTS_ROWS, 4),
    "churn_large": (CHURN_ROWS, 5),
}

_QUERY_BUILDERS = {
    "orders": orders_query,
    "measurements": measurements_query,
    "web_traffic_large": web_traffic_query,
    "geo_events_large": geo_events_query,
    "churn_large": churn_query,
}


def generate_all(out_dir: Path, force: bool) -> list[Path]:
    paths = []
    for name, (rows, seed_offset) in DATASETS.items():
        path = out_dir / f"{name}.csv"
        if not force and path.is_file():
            print(f"{name}: exists, reusing ({path})")
            paths.append(path)
            continue
        con = _connect(out_dir, seed_offset)
        try:
            query = _QUERY_BUILDERS[name](rows)
            count = _write(con, query, path)
            assert count == rows, (name, count, rows)
            mib = path.stat().st_size / 2**20
            print(f"{name}: {count:,} rows, {mib:.2f} MiB -> {path}")
        finally:
            con.close()
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output dir (default data/demo)"
    )
    parser.add_argument("--force", action="store_true", help="regenerate even if files exist")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    generate_all(args.out, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
