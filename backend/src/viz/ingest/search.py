"""Grid free-text search: case- and accent-insensitive "contains" ("zur" finds "Zürich").

The obvious SQL, `contains(strip_accents(lower(col)), strip_accents(lower(needle)))`, is cheap
on dictionary-encoded Parquet columns (DuckDB evaluates the function once per dictionary
entry) and on ASCII text, but `strip_accents` costs ~4-5x `lower` on high-cardinality
non-ASCII text (10M unique accented strings: ~1.0 s vs ~0.2 s). A case-insensitive RE2
pattern that expands each letter of the needle into a class of its accented forms
(`z[uùúûü...][\\x{300}-\\x{36f}]*r`) leaves the data untouched and runs at about `lower` speed
on such columns, but RE2 isn't evaluated per dictionary entry, so on dictionary columns it is
~10x slower than `strip_accents`.

So each text column gets the form that is cheap for its Parquet layout: `strip_accents` when
most of its row groups are dictionary-encoded, the pattern otherwise. The pattern is only used
for Latin, Greek and Cyrillic needles (`pattern_supported`); any other needle (Hangul,
Devanagari, Thai, CJK, ...) uses `strip_accents` on every column. Both forms match the same
rows, up to the small documented differences in `accent_pattern` (see
tests/ingest/test_search.py).
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from pathlib import Path

import pyarrow.parquet as pq
from cachetools import LRUCache
from sqlglot import exp

from viz.chartdata.sql import col, lit

log = logging.getLogger(__name__)

# Longer needles always use strip_accents. A pattern is ~170 bytes per letter of English text
# (each letter's class lists its accented forms; 43 KB at the cap); with the narrow mark class
# below RE2 stays in its fast DFA mode up to this cap (tests/ingest/test_search.py bounds
# size and time).
MAX_PATTERN_NEEDLE = 256

# Combining marks that can follow a pattern-safe (Latin/Greek/Cyrillic/Vietnamese) letter in
# decomposed text: Combining Diacritical Marks, Cyrillic titlo and related marks, extended and
# supplementary diacritics, marks for symbols, Cyrillic combining letters, and variation/half
# marks. (`\p{Mn}*` after every letter made long patterns big enough for RE2 to leave its DFA
# mode: a 43-letter needle took ~4 s per 1M rows instead of ~30 ms; this explicit, narrower set
# of ranges keeps the same speed while covering more scripts than the original Latin-only range.)
_MARKS = (
    "[\\x{300}-\\x{36f}\\x{483}-\\x{489}\\x{1ab0}-\\x{1aff}\\x{1dc0}-\\x{1dff}"
    "\\x{20d0}-\\x{20ff}\\x{2de0}-\\x{2dff}\\x{a66f}-\\x{a67d}\\x{a69e}\\x{a69f}"
    "\\x{fe00}-\\x{fe0f}\\x{fe20}-\\x{fe2f}]*"
)
_STRIPPED = frozenset({"Mn", "Mc", "Me"})


def _pattern_safe(cp: int) -> bool:
    """Code points the pattern form handles like `strip_accents(lower())`: ASCII, Latin,
    Greek and Cyrillic (below U+0530) and Latin/Greek Extended (U+1E00-U+1FFF). The tests
    check `fold` against DuckDB for every one of them. Other scripts always use
    strip_accents: NFD splits Hangul syllables into jamo (DuckDB doesn't), and many Indic and
    Southeast Asian vowel signs are spacing marks."""
    return cp < 0x530 or 0x1E00 <= cp <= 0x1FFF


def fold(text: str) -> str:
    """Python mirror of DuckDB's `strip_accents(lower(text))` for pattern-safe text: lower
    case, canonical decomposition, combining marks (Mn, Mc, Me) removed."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) not in _STRIPPED)


def _build_variants() -> dict[str, frozenset[str]]:
    """Folded character -> the pattern-safe precomposed characters that fold to it, e.g.
    "u" -> {ù, ú, û, ü, Ū, ...}."""
    found: dict[str, set[str]] = {}
    for cp in [*range(0x80, 0x530), *range(0x1E00, 0x2000)]:
        ch = chr(cp)
        base = fold(ch)
        if len(base) == 1 and base != ch and base != ch.lower():
            found.setdefault(base, set()).add(ch)
    return {k: frozenset(v) for k, v in found.items()}


# About 1,700 code points, so it's built at import (ms) rather than on the first search.
_VARIANTS = _build_variants()


def pattern_supported(needle: str) -> bool:
    """Whether `accent_pattern(needle)` may stand in for strip_accents."""
    if not needle or len(needle) > MAX_PATTERN_NEEDLE:
        return False
    if not all(_pattern_safe(ord(c)) for c in needle):
        return False
    folded = fold(needle)
    return bool(folded) and all(_pattern_safe(ord(c)) for c in folded)


def _escape(ch: str) -> str:
    if ch.isascii() and ch.isalnum():
        return ch
    return "\\x{" + format(ord(ch), "x") + "}"


def accent_pattern(needle: str) -> str:
    """A case-insensitive RE2 pattern matching text whose folded form contains
    `fold(needle)`. Only for needles that pass `pattern_supported`.

    Known, accepted differences from strip_accents:
    - RE2 `(?i)` case-folds within groups strip_accents doesn't, so the pattern matches a few
      more characters than it should: "s"/U+017F (long s)/U+1E9B, "µ"/Greek "μ", sigma forms
      (σ/ς/Σ), Greek symbol forms (ϐ ϑ ϕ ϖ ϰ ϱ ϵ), Cyrillic Extended-C (U+1C80-U+1C88), and
      U+0345 (combining ypogegrammeni) matching "ι".
    - A combining mark after a digit or symbol in the data isn't skipped ("12" doesn't match
      "1" + U+0301 + "2"), since only letters get `_MARKS` appended.
    - Exactly three precomposed characters that strip_accents folds to a pattern-safe form
      aren't matched by the pattern: U+2260 (≠ -> "="), U+226E (≮ -> "<"), U+226F (≯ -> ">").
      (U+212B ANGSTROM SIGN *is* matched: RE2's case-fold group puts it with "å".)
    See tests/ingest/test_search.py for the cases this pins."""
    parts = ["(?i)"]
    for ch in fold(needle):
        alts = _VARIANTS.get(ch)
        if alts:
            parts.append("[" + "".join(_escape(c) for c in sorted(alts | {ch})) + "]")
        else:
            parts.append(_escape(ch))
        if unicodedata.category(ch).startswith("L"):
            # Decomposed input ("u" + U+0308): strip_accents drops the marks too.
            parts.append(_MARKS)
    return "".join(parts)


_layouts_lock = threading.Lock()
_layouts: LRUCache[tuple[str, float, int], dict[str, bool]] = LRUCache(maxsize=256)


def dictionary_columns(path: Path) -> dict[str, bool]:
    """Column name -> whether most of its row groups are dictionary-encoded (footer only,
    cached per file). Empty when the footer can't be read."""
    try:
        stat = path.stat()
        cache_key = (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        return {}
    with _layouts_lock:
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
    with _layouts_lock:
        _layouts[cache_key] = result
    return result


def contains_folded(column: str, needle: str, *, dictionary: bool) -> exp.Expr:
    """Accent- and case-insensitive `column contains needle`, in the form that is cheap for
    the column's layout and exact for the needle's script."""
    if dictionary or not pattern_supported(needle):
        return exp.Anonymous(
            this="contains",
            expressions=[_strip_lower(col(column)), _strip_lower(lit(needle))],
        )
    return exp.Anonymous(
        this="regexp_matches", expressions=[col(column), lit(accent_pattern(needle))]
    )


def _strip_lower(value: exp.Expr) -> exp.Expr:
    return exp.Anonymous(this="strip_accents", expressions=[exp.Lower(this=value)])
