"""Errors raised by the statistics engine and its kernels."""

from __future__ import annotations

from viz.errors import InvalidSpecError


class StatsInputError(InvalidSpecError):
    """The input can't produce a meaningful result (no rows, wrong column type, singular design).

    Maps to HTTP 422. The message is shown to the user, so it says what to change.
    """
