"""Upgrades saved project documents to the current schema version.

When a contract change is not additive, bump CURRENT_SCHEMA_VERSION and register an upgrader
from the previous version. Upgraders work on raw JSON dicts (camelCase keys), before
validation, so old documents never need old model classes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CURRENT_SCHEMA_VERSION = 1

Doc = dict[str, Any]
Upgrader = Callable[[Doc], Doc]

# from_version → function producing a from_version + 1 document.
UPGRADERS: dict[int, Upgrader] = {}


class NewerSchemaError(ValueError):
    pass


def upgrade_project(doc: Doc) -> Doc:
    version = int(doc.get("schemaVersion", 1))
    if version > CURRENT_SCHEMA_VERSION:
        raise NewerSchemaError(
            f"project was saved by a newer version of Visualize (schema {version}, "
            f"this build supports {CURRENT_SCHEMA_VERSION})"
        )
    while version < CURRENT_SCHEMA_VERSION:
        doc = UPGRADERS[version](doc)
        version += 1
        doc["schemaVersion"] = version
    return doc
