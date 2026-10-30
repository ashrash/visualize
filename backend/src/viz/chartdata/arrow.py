"""Arrow normalization for tables sent to the browser (chart data and pipeline previews).

apache-arrow in the browser can't use some DuckDB output directly:
- DECIMAL (and HUGEINT/UHUGEINT, which DuckDB exports as decimal128(38, 0)) arrives as
  unscaled integers, so 12.50 shows as 1250 -> DOUBLE (`viz.engine.arrow.js_friendly`).
- TIME arrives as microseconds -> ISO text "HH:MM:SS[.fff]".
- INTERVAL has no usable JS representation -> text.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc

from viz.engine import js_friendly


def _time_text(column: pa.ChunkedArray) -> pa.ChunkedArray:
    text = pc.cast(column, pa.string())  # "HH:MM:SS.ffffff" (or without a fraction for time32[s])
    text = pc.replace_substring_regex(text, pattern=r"\.0+$", replacement="")
    return pc.replace_substring_regex(text, pattern=r"(\.\d{3})\d+$", replacement=r"\1")


def _interval_text(column: pa.ChunkedArray) -> pa.Array:
    values: list[str | None] = []
    for value in column.to_pylist():
        if value is None:
            values.append(None)
            continue
        months, days, nanos = value.months, value.days, value.nanoseconds
        parts = [f"{months} months" if months else "", f"{days} days" if days else ""]
        if nanos or not (months or days):
            parts.append(f"{nanos / 1e9:g} seconds")
        values.append(" ".join(p for p in parts if p))
    return pa.array(values, pa.string())


def browser_table(table: pa.Table) -> pa.Table:
    """`table` with every column in a type the frontend decodes correctly."""
    table = js_friendly(table)
    for i, field in enumerate(table.schema):
        if pa.types.is_time(field.type):
            replacement: pa.ChunkedArray | pa.Array = _time_text(table.column(i))
        elif pa.types.is_interval(field.type):
            replacement = _interval_text(table.column(i))
        else:
            continue
        table = table.set_column(i, pa.field(field.name, pa.string(), field.nullable), replacement)
    return table
