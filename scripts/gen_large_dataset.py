"""Generate a large, seeded perf fixture as CSV and Parquet (default 10,000,000 rows x 20 columns).

    uv run python scripts/gen_large_dataset.py --rows 10000000
    uv run python scripts/gen_large_dataset.py --rows 1000000 --out data/perf --seed 7

The shape matches samples/sales.csv (orders: id, date, region, product, channel, units, price,
sales) widened to 20 columns with numeric, categorical (7 to ~500k distinct values), temporal,
boolean and nullable columns. DuckDB generates and writes the data in streaming fashion, so
memory stays bounded; no pandas frame is ever built. Every value is a hash of (row index, seed),
so a given --rows/--seed always produces the same file, and `order_id` equals the 1-based row
number (the e2e grid test relies on that).

Output goes to the git-ignored data/perf/ by default: large_<rows>.csv and large_<rows>.parquet.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "perf"

COLUMNS = [
    "order_id",
    "order_ts",
    "order_date",
    "region",
    "country",
    "category",
    "product",
    "channel",
    "customer_id",
    "sku",
    "units",
    "unit_price",
    "sales",
    "discount",
    "score",
    "lat",
    "lon",
    "is_returned",
    "notes",
    "segment",
]


def build_query(rows: int, seed: int) -> str:
    """SELECT producing the 20 columns. `u(i, k)` is a uniform [0, 1) hash of (row, column k)."""
    customers = max(1_000, rows // 20)
    # Trusted integers only (this is a dev script, never fed user input).
    return f"""
    WITH t AS (
      SELECT
        i,
        u(i, 1) AS u_ts, u(i, 2) AS u_region, u(i, 3) AS u_country, u(i, 4) AS u_cat,
        u(i, 5) AS u_prod, u(i, 6) AS u_chan, u(i, 7) AS u_cust, u(i, 8) AS u_sku,
        u(i, 9) AS u_units, u(i, 10) AS u_p1, u(i, 11) AS u_p2, u(i, 12) AS u_disc_on,
        u(i, 13) AS u_disc, u(i, 14) AS u_s1, u(i, 15) AS u_s2, u(i, 16) AS u_lat,
        u(i, 17) AS u_lon, u(i, 18) AS u_ret, u(i, 19) AS u_note, u(i, 20) AS u_seg
      FROM range({rows}) AS r(i)
    ),
    c AS (
      SELECT *,
        1 + least(99, floor(-ln(1 - u_units) * 3))::INTEGER AS units_v,
        round(exp(2.5 + 0.8 * sqrt(-2 * ln(1 - u_p1)) * cos(2 * pi() * u_p2)), 2) AS price_v,
        CASE WHEN u_disc_on < 0.25
             THEN list_extract([0.05, 0.1, 0.15, 0.2, 0.3]::DOUBLE[],
                               1 + floor(u_disc * 5)::INTEGER)
        END AS discount_v
      FROM t
    )
    SELECT
      (i + 1)::BIGINT AS order_id,
      TIMESTAMP '2020-01-01' + to_seconds(floor(u_ts * 5 * 365 * 86400)::BIGINT) AS order_ts,
      CAST(TIMESTAMP '2020-01-01' + to_seconds(floor(u_ts * 5 * 365 * 86400)::BIGINT) AS DATE)
        AS order_date,
      list_extract(['North', 'South', 'East', 'West', 'Central', 'Overseas', 'Online'],
                   1 + floor(u_region * 7)::INTEGER) AS region,
      'Country ' || lpad((floor(pow(u_country, 1.6) * 40)::INTEGER + 1)::VARCHAR, 2, '0')
        AS country,
      'Category ' || lpad((floor(pow(u_cat, 1.8) * 12)::INTEGER + 1)::VARCHAR, 2, '0')
        AS category,
      'Product ' || lpad((floor(pow(u_prod, 1.3) * 200)::INTEGER + 1)::VARCHAR, 4, '0')
        AS product,
      list_extract(['Retail', 'Online', 'Wholesale', 'Partner'], 1 + floor(u_chan * 4)::INTEGER)
        AS channel,
      'C' || lpad((floor(pow(u_cust, 1.3) * {customers})::BIGINT + 1)::VARCHAR, 7, '0')
        AS customer_id,
      'SKU-' || lpad((floor(u_sku * 5000)::INTEGER + 1)::VARCHAR, 4, '0') AS sku,
      units_v AS units,
      price_v AS unit_price,
      round(units_v * price_v * (1 - coalesce(discount_v, 0)), 2) AS sales,
      discount_v AS discount,
      round(sqrt(-2 * ln(1 - u_s1)) * cos(2 * pi() * u_s2), 4) AS score,
      round(-60 + u_lat * 130, 4) AS lat,
      round(-180 + u_lon * 360, 4) AS lon,
      u_ret < 0.04 AS is_returned,
      CASE WHEN u_note < 0.15 THEN 'note ' || (floor(u_note * 6666))::INTEGER::VARCHAR END
        AS notes,
      CASE WHEN u_seg < 0.05 THEN NULL
           ELSE list_extract(['Consumer', 'SMB', 'Enterprise'],
                             1 + floor((u_seg - 0.05) / 0.95 * 3)::INTEGER)
      END AS segment
    FROM c
    """


def generate(rows: int, out_dir: Path, seed: int, force: bool = False) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"large_{rows}.csv"
    parquet_path = out_dir / f"large_{rows}.parquet"
    if not force and csv_path.is_file() and parquet_path.is_file():
        print(f"fixture exists, reusing: {csv_path.name} and {parquet_path.name}")
        return csv_path, parquet_path

    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{(out_dir / 'tmp').as_posix()}'")
    # A seeded uniform: top 53 bits of a 64-bit hash of (row, column, seed) -> [0, 1).
    con.execute(
        f"CREATE MACRO u(i, k) AS (hash(i * 31 + k * 1000003 + {int(seed)}) >> 11)::DOUBLE "
        "/ 9007199254740992.0"
    )
    query = build_query(rows, seed)

    started = time.perf_counter()
    tmp_parquet = parquet_path.with_suffix(".parquet.part")
    con.execute(
        f"COPY ({query}) TO '{tmp_parquet.as_posix()}' "
        "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1048576)"
    )
    tmp_parquet.replace(parquet_path)
    parquet_s = time.perf_counter() - started

    started = time.perf_counter()
    tmp_csv = csv_path.with_suffix(".csv.part")
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{parquet_path.as_posix()}')) "
        f"TO '{tmp_csv.as_posix()}' (FORMAT csv, HEADER)"
    )
    tmp_csv.replace(csv_path)
    csv_s = time.perf_counter() - started

    source = f"read_parquet('{parquet_path.as_posix()}')"
    checked = con.execute(f"SELECT count(*), min(order_id), max(order_id) FROM {source}").fetchone()
    con.close()
    assert checked is not None and checked[0] == rows and checked[2] == rows, checked

    parquet_mib = parquet_path.stat().st_size / 2**20
    csv_mib = csv_path.stat().st_size / 2**20
    print(f"parquet: {parquet_path.name}  {parquet_mib:9.1f} MiB  {parquet_s:6.1f} s")
    print(f"csv:     {csv_path.name}  {csv_mib:9.1f} MiB  {csv_s:6.1f} s")
    total_s = parquet_s + csv_s
    print(f"rows: {rows:,}  columns: {len(COLUMNS)}  seed: {seed}  total: {total_s:.1f} s")
    return csv_path, parquet_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--rows", type=int, default=10_000_000, help="row count (default 10,000,000)"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output directory (default data/perf)"
    )
    parser.add_argument("--seed", type=int, default=20261030, help="seed (default 20261030)")
    parser.add_argument("--force", action="store_true", help="regenerate even if the files exist")
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("--rows must be positive")
    generate(args.rows, args.out, args.seed, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
