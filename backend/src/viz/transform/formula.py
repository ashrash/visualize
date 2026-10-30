"""Formula validation: a DuckDB expression subset checked against an explicit allowlist.

Formulas (calculated columns, filter expressions) are user text, so they are handled like
hostile input:

1. Parse with SQLGlot. The text must be exactly one expression, at most MAX_LENGTH chars.
2. Walk the AST. Every node class must be allowlisted (`_STRUCTURAL`, or a function class
   derived from `FUNCTIONS`); unknown functions (`exp.Anonymous`) must be allowlisted by
   name. Everything else is rejected by default: subqueries, statements, table functions,
   `x.f()` method calls, placeholders, lambdas and JSON arrows, windows, stars, comments.
3. Column references must be single, unqualified names that exist in the input schema.
4. The SQL that runs is regenerated from the validated AST with every identifier quoted; the
   user's text never reaches DuckDB.

Excluded on purpose: nondeterministic functions (now, random, uuid; they would poison the
cache), functions with unbounded output (repeat, range, lpad), list/struct/lambda functions,
and every table, system or settings function (read_*, glob, getenv, query, current_setting).
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from viz.chartdata.sql import ident

MAX_LENGTH = 4000
MAX_NODES = 2000
# Column references per formula: `s || s || ...` grows text by the number of references.
MAX_COLUMN_REFS = 100
MAX_DEPTH = 64

Category = Literal["math", "string", "date", "conditional", "null", "type"]


@dataclass(frozen=True)
class FunctionDoc:
    name: str
    category: Category
    signature: str
    description: str
    # A call using columns n (DOUBLE), s (VARCHAR), d (TIMESTAMP), b (BOOLEAN). It defines
    # which SQLGlot node the name parses to, and the tests run it.
    example: str


def _f(
    name: str, category: Category, signature: str, description: str, example: str
) -> FunctionDoc:
    return FunctionDoc(name, category, signature, description, example)


FUNCTIONS: tuple[FunctionDoc, ...] = (
    # math
    _f("abs", "math", "abs(x)", "Absolute value", "abs(n)"),
    _f("ceil", "math", "ceil(x)", "Round up to an integer", "ceil(n)"),
    _f("floor", "math", "floor(x)", "Round down to an integer", "floor(n)"),
    _f("round", "math", "round(x, digits)", "Round to the given decimal places", "round(n, 2)"),
    _f("trunc", "math", "trunc(x)", "Drop the fractional part", "trunc(n)"),
    _f("sqrt", "math", "sqrt(x)", "Square root", "sqrt(n)"),
    _f("cbrt", "math", "cbrt(x)", "Cube root", "cbrt(n)"),
    _f("exp", "math", "exp(x)", "e raised to x", "exp(n)"),
    _f("ln", "math", "ln(x)", "Natural logarithm", "ln(n)"),
    _f("log10", "math", "log10(x)", "Base-10 logarithm", "log10(n)"),
    _f("log2", "math", "log2(x)", "Base-2 logarithm", "log2(n)"),
    _f("pow", "math", "pow(x, y)", "x raised to y", "pow(n, 2)"),
    _f("power", "math", "power(x, y)", "x raised to y", "power(n, 2)"),
    _f("sign", "math", "sign(x)", "-1, 0 or 1", "sign(n)"),
    _f("greatest", "math", "greatest(a, b, ...)", "Largest argument", "greatest(n, 1)"),
    _f("least", "math", "least(a, b, ...)", "Smallest argument", "least(n, 1)"),
    _f("mod", "math", "mod(x, y)", "Remainder of x / y", "mod(n, 3)"),
    _f("pi", "math", "pi()", "The constant pi", "pi()"),
    _f("sin", "math", "sin(x)", "Sine (radians)", "sin(n)"),
    _f("cos", "math", "cos(x)", "Cosine (radians)", "cos(n)"),
    _f("tan", "math", "tan(x)", "Tangent (radians)", "tan(n)"),
    _f("asin", "math", "asin(x)", "Arcsine", "asin(n / 100)"),
    _f("acos", "math", "acos(x)", "Arccosine", "acos(n / 100)"),
    _f("atan", "math", "atan(x)", "Arctangent", "atan(n)"),
    _f("atan2", "math", "atan2(y, x)", "Arctangent of y / x", "atan2(n, 1)"),
    _f("degrees", "math", "degrees(x)", "Radians to degrees", "degrees(n)"),
    _f("radians", "math", "radians(x)", "Degrees to radians", "radians(n)"),
    _f("isnan", "math", "isnan(x)", "True if x is NaN", "isnan(n)"),
    _f("isinf", "math", "isinf(x)", "True if x is infinite", "isinf(n)"),
    _f("isfinite", "math", "isfinite(x)", "True if x is finite", "isfinite(n)"),
    # string
    _f("lower", "string", "lower(s)", "Lower case", "lower(s)"),
    _f("upper", "string", "upper(s)", "Upper case", "upper(s)"),
    _f("length", "string", "length(s)", "Number of characters", "length(s)"),
    _f("trim", "string", "trim(s)", "Strip surrounding spaces", "trim(s)"),
    _f("ltrim", "string", "ltrim(s)", "Strip leading spaces", "ltrim(s)"),
    _f("rtrim", "string", "rtrim(s)", "Strip trailing spaces", "rtrim(s)"),
    _f(
        "substring",
        "string",
        "substring(s, start, length)",
        "Part of a string (1-based)",
        "substring(s, 1, 3)",
    ),
    _f("left", "string", "left(s, n)", "First n characters", "left(s, 2)"),
    _f("right", "string", "right(s, n)", "Last n characters", "right(s, 2)"),
    _f(
        "replace",
        "string",
        "replace(s, 'from', 'to')",
        "Replace every occurrence (quoted text up to 64 characters; not nested)",
        "replace(s, 'a', 'b')",
    ),
    _f(
        "concat", "string", "concat(a, b, ...)", "Join strings (NULLs skipped)", "concat(s, '-', s)"
    ),
    _f(
        "concat_ws",
        "string",
        "concat_ws(sep, a, b, ...)",
        "Join strings with a separator",
        "concat_ws('-', s, s)",
    ),
    _f("contains", "string", "contains(s, part)", "True if s contains part", "contains(s, 'a')"),
    _f(
        "starts_with",
        "string",
        "starts_with(s, prefix)",
        "True if s starts with prefix",
        "starts_with(s, 'a')",
    ),
    _f(
        "ends_with",
        "string",
        "ends_with(s, suffix)",
        "True if s ends with suffix",
        "ends_with(s, 'a')",
    ),
    _f(
        "strpos",
        "string",
        "strpos(s, part)",
        "Position of part in s (0 if absent)",
        "strpos(s, 'a')",
    ),
    _f(
        "split_part",
        "string",
        "split_part(s, sep, index)",
        "The index-th piece (1-based)",
        "split_part(s, '-', 1)",
    ),
    _f(
        "regexp_matches",
        "string",
        "regexp_matches(s, pattern)",
        "True if the RE2 pattern matches",
        "regexp_matches(s, 'a+')",
    ),
    _f(
        "regexp_replace",
        "string",
        "regexp_replace(s, 'pattern', 'replacement')",
        "Replace the first RE2 match (quoted text up to 64 characters; not nested)",
        "regexp_replace(s, 'a', 'b')",
    ),
    _f(
        "regexp_extract",
        "string",
        "regexp_extract(s, pattern)",
        "The first RE2 match",
        "regexp_extract(s, '[0-9]+')",
    ),
    _f("reverse", "string", "reverse(s)", "Characters in reverse order", "reverse(s)"),
    _f("strip_accents", "string", "strip_accents(s)", "Remove accents", "strip_accents(s)"),
    # date
    _f(
        "date_trunc",
        "date",
        "date_trunc(part, d)",
        "Truncate to year, month, day, ...",
        "date_trunc('month', d)",
    ),
    _f(
        "date_part",
        "date",
        "date_part(part, d)",
        "A part of a date as a number",
        "date_part('year', d)",
    ),
    _f(
        "extract",
        "date",
        "extract(part FROM d)",
        "A part of a date as a number",
        "extract(year FROM d)",
    ),
    _f("year", "date", "year(d)", "Year", "year(d)"),
    _f("quarter", "date", "quarter(d)", "Quarter (1-4)", "quarter(d)"),
    _f("month", "date", "month(d)", "Month (1-12)", "month(d)"),
    _f("week", "date", "week(d)", "ISO week number", "week(d)"),
    _f("day", "date", "day(d)", "Day of the month", "day(d)"),
    _f("dayofweek", "date", "dayofweek(d)", "Day of the week (0 = Sunday)", "dayofweek(d)"),
    _f("dayofyear", "date", "dayofyear(d)", "Day of the year", "dayofyear(d)"),
    _f("hour", "date", "hour(d)", "Hour", "hour(d)"),
    _f("minute", "date", "minute(d)", "Minute", "minute(d)"),
    _f("second", "date", "second(d)", "Second", "second(d)"),
    _f("monthname", "date", "monthname(d)", "English month name", "monthname(d)"),
    _f("dayname", "date", "dayname(d)", "English weekday name", "dayname(d)"),
    _f("last_day", "date", "last_day(d)", "Last day of the month", "last_day(d)"),
    _f(
        "date_diff",
        "date",
        "date_diff(part, start, end)",
        "Number of part boundaries crossed",
        "date_diff('day', d, d)",
    ),
    _f(
        "datediff",
        "date",
        "datediff(part, start, end)",
        "Alias of date_diff",
        "datediff('day', d, d)",
    ),
    _f(
        "date_add",
        "date",
        "date_add(d, interval)",
        "Add an interval",
        "date_add(d, INTERVAL 1 DAY)",
    ),
    _f("strftime", "date", "strftime(d, format)", "Format a date as text", "strftime(d, '%Y-%m')"),
    _f(
        "strptime",
        "date",
        "strptime(s, format)",
        "Parse text as a timestamp (error if invalid)",
        "strptime('2024-01-05', '%Y-%m-%d')",
    ),
    _f(
        "try_strptime",
        "date",
        "try_strptime(s, format)",
        "Parse text as a timestamp (NULL if invalid)",
        "try_strptime(s, '%Y-%m-%d')",
    ),
    _f(
        "make_date", "date", "make_date(year, month, day)", "Build a date", "make_date(2024, 1, 31)"
    ),
    _f(
        "make_timestamp",
        "date",
        "make_timestamp(y, m, d, hh, mm, ss)",
        "Build a timestamp",
        "make_timestamp(2024, 1, 31, 12, 0, 0)",
    ),
    _f("epoch", "date", "epoch(d)", "Seconds since 1970-01-01", "epoch(d)"),
    _f("epoch_ms", "date", "epoch_ms(d)", "Milliseconds since 1970-01-01", "epoch_ms(d)"),
    _f(
        "to_timestamp",
        "date",
        "to_timestamp(seconds)",
        "Timestamp from epoch seconds",
        "to_timestamp(n)",
    ),
    # conditional
    _f(
        "if", "conditional", "if(condition, then, else)", "Pick a value by condition", "if(b, 1, 0)"
    ),
    # null handling
    _f("coalesce", "null", "coalesce(a, b, ...)", "First non-NULL argument", "coalesce(n, 0)"),
    _f("ifnull", "null", "ifnull(a, b)", "b when a is NULL", "ifnull(n, 0)"),
    _f("nullif", "null", "nullif(a, b)", "NULL when a = b", "nullif(n, 0)"),
    # type
    _f("typeof", "type", "typeof(x)", "Name of the value's type", "typeof(n)"),
)

# Expression syntax (not function calls) that formulas may use.
_STRUCTURAL: frozenset[type[exp.Expr]] = frozenset(
    {
        exp.Column,
        exp.Identifier,
        exp.Literal,
        exp.Boolean,
        exp.Null,
        exp.Paren,
        exp.Neg,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Div,
        exp.IntDiv,
        exp.Mod,
        exp.Pow,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.NullSafeEQ,
        exp.NullSafeNEQ,
        exp.And,
        exp.Or,
        exp.Not,
        exp.Is,
        exp.In,
        exp.Between,
        exp.Like,
        exp.ILike,
        exp.Case,
        exp.If,
        exp.Cast,
        exp.TryCast,
        exp.DataType,
        exp.DataTypeParam,
        exp.DPipe,
        exp.Interval,
        exp.Var,
    }
)

# Var nodes print as raw text, so they're limited to date-part and interval unit names.
_DATE_PARTS = frozenset(
    {
        "YEAR",
        "YEARS",
        "QUARTER",
        "QUARTERS",
        "MONTH",
        "MONTHS",
        "WEEK",
        "WEEKS",
        "DAY",
        "DAYS",
        "HOUR",
        "HOURS",
        "MINUTE",
        "MINUTES",
        "SECOND",
        "SECONDS",
        "MILLISECOND",
        "MILLISECONDS",
        "MICROSECOND",
        "MICROSECONDS",
        "DOW",
        "DOY",
        "ISODOW",
        "DAYOFWEEK",
        "DAYOFYEAR",
        "EPOCH",
        "DECADE",
        "CENTURY",
        "MILLENNIUM",
        "ISOYEAR",
    }
)

_CAST_TYPES = frozenset(
    {
        exp.DataType.Type.TINYINT,
        exp.DataType.Type.SMALLINT,
        exp.DataType.Type.INT,
        exp.DataType.Type.BIGINT,
        exp.DataType.Type.INT128,
        exp.DataType.Type.FLOAT,
        exp.DataType.Type.DOUBLE,
        exp.DataType.Type.DECIMAL,
        exp.DataType.Type.VARCHAR,
        exp.DataType.Type.TEXT,
        exp.DataType.Type.BOOLEAN,
        exp.DataType.Type.DATE,
        exp.DataType.Type.TIMESTAMP,
        exp.DataType.Type.TIME,
    }
)


def _function_allowlist() -> tuple[frozenset[type[exp.Expr]], frozenset[str]]:
    classes: set[type[exp.Expr]] = set()
    names: set[str] = set()
    for doc in FUNCTIONS:
        node = sqlglot.parse_one(doc.example, read="duckdb")
        if isinstance(node, exp.Anonymous):
            names.add(doc.name)
        elif type(node) in _STRUCTURAL:
            pass  # e.g. mod(a, b) parses to the % operator
        elif isinstance(node, exp.Func):
            classes.add(type(node))
        else:  # pragma: no cover - guarded by tests
            raise AssertionError(f"example for {doc.name} is not a function call")
    return frozenset(classes), frozenset(names)


_FUNC_CLASSES, _FUNC_NAMES = _function_allowlist()


class FormulaError(Exception):
    def __init__(self, message: str, start: int | None = None, end: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.start = start
        self.end = end


@dataclass(frozen=True)
class Formula:
    expression: exp.Expr
    # Resolved (schema-cased) names of the columns the formula reads.
    fields: tuple[str, ...]


def _span(node: exp.Expr) -> tuple[int | None, int | None]:
    target = node.this if isinstance(node, exp.Column) else node
    meta = getattr(target, "meta", None) or {}
    start, end = meta.get("start"), meta.get("end")
    if isinstance(start, int) and isinstance(end, int):
        return start, end + 1
    return None, None


def _parse(text: str) -> exp.Expr:
    if not text.strip():
        raise FormulaError("the formula is empty")
    if len(text) > MAX_LENGTH:
        raise FormulaError(f"the formula is longer than {MAX_LENGTH} characters")
    try:
        parsed = sqlglot.parse(text, read="duckdb")
    except (ParseError, TokenError) as exc:
        start = None
        errors = getattr(exc, "errors", None)
        if errors and "\n" not in text:
            col_no = errors[0].get("col")
            start = int(col_no) - 1 if isinstance(col_no, int) and col_no > 0 else None
        raise FormulaError(f"can't parse the formula: {_first_line(exc)}", start) from exc
    except RecursionError as exc:
        raise FormulaError("the formula is nested too deeply") from exc
    statements = [p for p in parsed if p is not None]
    if len(statements) != 1 or len(parsed) != 1:
        raise FormulaError("a formula must be a single expression (no ';')")
    return statements[0]


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def _check_node(node: exp.Expr) -> None:
    cls = type(node)
    if isinstance(node, exp.Anonymous):
        name = node.name.lower() if isinstance(node.this, str) else ""
        if name not in _FUNC_NAMES:
            raise FormulaError(f"function {node.name or '?'}() is not allowed", *_span(node))
        return
    if cls in _FUNC_CLASSES:
        return
    if cls not in _STRUCTURAL:
        if isinstance(node, exp.Func):
            raise FormulaError(f"function {node.sql_name().lower()}() is not allowed")
        raise FormulaError(f"{_describe_node(node)} is not allowed in a formula", *_span(node))
    if isinstance(node, exp.Column):
        if any(node.args.get(part) for part in ("table", "db", "catalog")):
            raise FormulaError("use plain column names (no table prefixes)", *_span(node))
        if not isinstance(node.this, exp.Identifier):
            raise FormulaError("unsupported column reference", *_span(node))
    elif isinstance(node, exp.In):
        if node.args.get("query") or node.args.get("unnest") or node.args.get("field"):
            raise FormulaError("IN accepts a list of values only")
    elif isinstance(node, exp.Var):
        if str(node.this).upper() not in _DATE_PARTS:
            raise FormulaError(f"unknown date part {node.this!s}")
    elif isinstance(node, exp.DataType):
        if node.this not in _CAST_TYPES:
            raise FormulaError(f"can't cast to {node.sql(dialect='duckdb')}")
    elif isinstance(node, exp.Literal) and not node.is_string:
        # Number literals print verbatim; make sure the text really is a number.
        try:
            float(str(node.this))
        except ValueError as exc:
            raise FormulaError(f"invalid number {node.this!s}") from exc


def _describe_node(node: exp.Expr) -> str:
    if isinstance(node, exp.Query | exp.Subquery):
        return "a subquery"
    if isinstance(node, exp.Dot):
        return "method-call syntax (x.f())"
    if isinstance(node, exp.Placeholder):
        return "a parameter placeholder"
    if isinstance(node, exp.Star):
        return "*"
    if isinstance(node, exp.Window):
        return "a window function"
    return type(node).__name__


def _check_shape(tree: exp.Expr) -> None:
    count = 0
    stack: list[tuple[exp.Expr, int]] = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES:
            raise FormulaError("the formula is too long")
        if depth > MAX_DEPTH:
            raise FormulaError("the formula is nested too deeply")
        stack.extend((child, depth + 1) for child in node.iter_expressions())


# String functions whose output can be much longer than their input. Nesting them compounds
# the growth (nine nested replaces in a 253-character formula made a 10^10-character string),
# so they take only short literal patterns/replacements, can't contain one another, and are
# limited in number. Every other allowlisted string function's output is bounded by its
# inputs plus the formula's own length.
_GROWING: tuple[type[exp.Expr], ...] = (exp.Replace, exp.RegexpReplace)
MAX_GROWING_CALLS = 8
MAX_REPLACE_LITERAL = 64


def _check_growth(tree: exp.Expr) -> None:
    calls = [node for node in tree.walk() if isinstance(node, _GROWING)]
    if len(calls) > MAX_GROWING_CALLS:
        raise FormulaError(f"a formula can use replace at most {MAX_GROWING_CALLS} times")
    for call in calls:
        name = "regexp_replace" if isinstance(call, exp.RegexpReplace) else "replace"
        inner = call.args.get("this")
        if isinstance(inner, exp.Expr) and any(isinstance(node, _GROWING) for node in inner.walk()):
            raise FormulaError(f"{name}() can't be nested inside another replace")
        for arg_name, arg in call.args.items():
            if arg_name == "this" or not isinstance(arg, exp.Expr):
                continue
            if not (isinstance(arg, exp.Literal) and arg.is_string):
                raise FormulaError(f"{name}() needs a quoted text pattern and replacement")
            if len(str(arg.this)) > MAX_REPLACE_LITERAL:
                raise FormulaError(
                    f"{name}() patterns and replacements are limited to "
                    f"{MAX_REPLACE_LITERAL} characters"
                )


def validate_formula(text: str, columns: Sequence[str] | Mapping[str, object]) -> Formula:
    """Validate `text` against the input `columns`; return a safe, regenerated expression."""
    tree = _parse(text)
    _check_shape(tree)
    by_lower: dict[str, str] = {}
    for name in columns:
        by_lower.setdefault(name.lower(), name)
    fields: list[str] = []
    for node in list(tree.walk()):
        _check_node(node)
        node.comments = None
    _check_growth(tree)
    columns_used = list(tree.find_all(exp.Column))
    if len(columns_used) > MAX_COLUMN_REFS:
        raise FormulaError(f"a formula can refer to columns at most {MAX_COLUMN_REFS} times")
    for column in columns_used:
        written = column.name
        actual = by_lower.get(written.lower())
        if actual is None:
            close = difflib.get_close_matches(written, list(columns), n=1)
            hint = f"; did you mean {close[0]!r}?" if close else ""
            raise FormulaError(f"unknown column {written!r}{hint}", *_span(column))
        column.set("this", ident(actual))
        if actual not in fields:
            fields.append(actual)
    return Formula(expression=tree, fields=tuple(fields))


def function_docs() -> list[FunctionDoc]:
    return sorted(FUNCTIONS, key=lambda d: (d.category, d.name))
