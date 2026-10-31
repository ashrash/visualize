"""Grid free-text search: case- and accent-insensitive "contains" ("zur" finds "Zürich").

The obvious SQL, `contains(strip_accents(lower(col)), fold(needle))`, is cheap on
dictionary-encoded Parquet columns (DuckDB evaluates the function once per dictionary entry)
and on ASCII text, but `strip_accents` costs ~4-5x `lower` on high-cardinality non-ASCII text
(10M unique accented strings: ~1.0 s vs ~0.2 s). A case-insensitive RE2 pattern that expands
each letter of the needle into a class of its accented forms (`z[uùúûü...]\\p{Mn}*r`) leaves
the data untouched and runs at about `lower` speed on such columns, but RE2 isn't evaluated
per dictionary entry, so on dictionary columns it is ~10x slower than `strip_accents`.

So each text column gets the form that is cheap for its Parquet layout: `strip_accents` when
most of its row groups are dictionary-encoded, the pattern otherwise. Both match the same
rows (see tests/ingest/test_search.py).
"""

from __future__ import annotations

import functools
import logging
import unicodedata
from pathlib import Path

import pyarrow.parquet as pq
from cachetools import LRUCache
from sqlglot import exp

from viz.chartdata.sql import col, lit

log = logging.getLogger(__name__)

# Needles longer than this always use strip_accents: the pattern grows ~40 bytes per letter.
MAX_PATTERN_NEEDLE = 256


def fold(text: str) -> str:
    """Python mirror of DuckDB's `strip_accents(lower(text))`: lower case, canonical
    decomposition, nonspacing marks removed."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


@functools.cache
def _variants() -> dict[str, frozenset[str]]:
    """Folded character -> the precomposed characters (BMP) that fold to it, e.g. "u" ->
    {ù, ú, û, ü, Ū, ...}."""
    found: dict[str, set[str]] = {}
    for cp in range(0x80, 0x10000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        base = fold(ch)
        if len(base) == 1 and base != ch and base != ch.lower():
            found.setdefault(base, set()).add(ch)
    return {k: frozenset(v) for k, v in found.items()}


def _escape(ch: str) -> str:
    if ch.isascii() and ch.isalnum():
        return ch
    return f"\\x{{{ord(ch):x}}}"


def accent_pattern(needle: str) -> str:
    """A case-insensitive RE2 pattern matching text whose folded form contains
    `fold(needle)`."""
    variants = _variants()
    parts = ["(?i)"]
    for ch in fold(needle):
        alts = variants.get(ch)
        if alts:
            parts.append("[" + "".join(_escape(c) for c in sorted(alts | {ch})) + "]")
        else:
            parts.append(_escape(ch))
        if unicodedata.category(ch).startswith("L"):
            # Decomposed input ("u" + U+0308): strip_accents drops the marks too.
            parts.append(r"\p{Mn}*")
    return "".join(parts)


_layouts: LRUCache[tuple[str, float, int], dict[str, bool]] = LRUCache(maxsize=256)


def dictionary_columns(path: Path) -> dict[str, bool]:
    """Column name -> whether most of its row groups are dictionary-encoded (footer only,
    cached per file). Empty when the footer can't be read."""
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        return {}
    found = _layouts.get(cache_key)
    if found is not None:
        return found
    try:
        meta = pq.read_metadata(path)
    except (OSError, ValueError):
        log.info("can't read the Parquet footer of %s", path, exc_info=True)
        return {}
    votes: dict[str, int] = {}
    for i in range(meta.num_row_groups):
        group = meta.row_group(i)
        for j in range(group.num_columns):
            chunk = group.column(j)
            votes[chunk.path_in_schema] = votes.get(chunk.path_in_schema, 0) + (
                1 if chunk.has_dictionary_page else -1
            )
    result = {name: score > 0 for name, score in votes.items()}
    _layouts[cache_key] = result
    return result


def contains_folded(column: str, needle: str, *, dictionary: bool) -> exp.Expr:
    """Accent- and case-insensitive `column contains needle`, in the form that is cheap for
    the column's layout."""
    if dictionary or len(needle) > MAX_PATTERN_NEEDLE:
        return exp.Anonymous(
            this="contains",
            expressions=[_strip_lower(col(column)), _strip_lower(lit(needle))],
        )
    return exp.Anonymous(
        this="regexp_matches", expressions=[col(column), lit(accent_pattern(needle))]
    )


def _strip_lower(value: exp.Expr) -> exp.Expr:
    return exp.Anonymous(this="strip_accents", expressions=[exp.Lower(this=value)])
