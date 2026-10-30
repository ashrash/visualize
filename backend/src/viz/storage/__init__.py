"""Where data files live. The only module that knows about local disk (and later S3)."""

from viz.storage.local import LocalStorage

__all__ = ["LocalStorage"]
