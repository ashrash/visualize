"""Base model and small shared types for every contract.

Contracts are camelCase on the wire (JSON, OpenAPI, TypeScript) and snake_case in Python.
Unknown fields are rejected so drift between frontend and backend fails loudly.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Contract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="forbid",
    )


def new_id() -> str:
    return str(uuid.uuid4())


# JSON scalar used in filters and fill values. Dates and timestamps travel as ISO-8601 strings.
Scalar = str | int | float | bool | None


class SourceRef(Contract):
    """Points at the data a chart, pipeline step or widget reads."""

    kind: Literal["dataset", "pipeline"]
    id: str


class AggFn(StrEnum):
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    STD = "std"
    VAR = "var"
