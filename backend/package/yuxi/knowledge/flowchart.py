"""Small, shared flowchart-ingestion invariants."""

from __future__ import annotations

from typing import Any

FLOWCHART_INGESTION_TYPE = "flowchart"
FLOWCHART_METADATA_KEY = "flowchart"


def is_flowchart(value: Any) -> bool:
    """Return whether a file record or metadata mapping uses flowchart ingestion."""
    if isinstance(value, dict):
        params = value.get("processing_params")
    else:
        params = getattr(value, "processing_params", None)
    return isinstance(params, dict) and params.get("ingestion_type") == FLOWCHART_INGESTION_TYPE


def flowchart_revision(value: Any) -> int:
    if isinstance(value, dict):
        parse_metadata = value.get("parse_metadata")
    else:
        parse_metadata = getattr(value, "parse_metadata", None)
    metadata = (parse_metadata or {}).get(FLOWCHART_METADATA_KEY, {}) if isinstance(parse_metadata, dict) else {}
    return max(0, int(metadata.get("revision") or 0))


__all__ = [
    "FLOWCHART_INGESTION_TYPE",
    "FLOWCHART_METADATA_KEY",
    "flowchart_revision",
    "is_flowchart",
]
