"""Arrow IPC serialization for grid pages and chart data."""

from __future__ import annotations

import pyarrow as pa
from pydantic import BaseModel

from viz.contracts import ARROW_META_KEY


def js_friendly(table: pa.Table) -> pa.Table:
    """Cast types the browser can't use directly. Decimals (and HUGEINT) become float64."""
    fields = []
    changed = False
    for field in table.schema:
        if pa.types.is_decimal(field.type):
            fields.append(pa.field(field.name, pa.float64(), field.nullable))
            changed = True
        else:
            fields.append(field)
    if not changed:
        return table
    return table.cast(pa.schema(fields, metadata=table.schema.metadata))


def ipc_bytes(table: pa.Table, meta: BaseModel) -> bytes:
    """Serialize as an Arrow IPC stream with `meta` as JSON in the schema metadata."""
    metadata = dict(table.schema.metadata or {})
    metadata[ARROW_META_KEY.encode()] = meta.model_dump_json().encode()
    table = js_friendly(table).replace_schema_metadata(metadata)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    data: bytes = sink.getvalue().to_pybytes()
    return data
