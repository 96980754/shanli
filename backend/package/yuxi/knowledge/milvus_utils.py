from __future__ import annotations


def escape_milvus_string_literal(value: str) -> str:
    """Escape one value embedded in a quoted Milvus string literal."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')
