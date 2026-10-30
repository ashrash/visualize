"""Domain errors. The app maps each to an HTTP status with a readable `detail` message."""

from __future__ import annotations


class VizError(Exception):
    status_code = 400


class NotFoundError(VizError):
    status_code = 404


class ConflictError(VizError):
    status_code = 409


class PayloadTooLargeError(VizError):
    status_code = 413


class UnsupportedFormatError(VizError):
    status_code = 415


class InvalidSpecError(VizError):
    status_code = 422


class NotImplementedYetError(VizError):
    status_code = 501


class EngineBusyError(VizError):
    status_code = 503


class QueryInterruptedError(VizError):
    """A query was stopped: superseded by a newer request (409) or timed out (504)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"query {reason}")
        self.reason = reason
        self.status_code = 504 if reason == "timed out" else 409
