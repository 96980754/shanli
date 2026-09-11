from __future__ import annotations

import re
import unicodedata
from pathlib import PurePath

_KNOWN_EXTENSION = re.compile(r"\.(?:docx?|pdf|pptx?|xlsx?|csv|md|txt|wps)$", re.IGNORECASE)
_DECIMAL_VERSION = re.compile(r"[-_\s]*(?:v|版本)?\s*(?P<version>\d+(?:\.\d+)+)\s*$", re.IGNORECASE)
_INTEGER_VERSION = re.compile(r"[-_\s]+(?:v|版本)\s*(?P<version>\d+)\s*$", re.IGNORECASE)


def parse_filename_version(filename: str) -> tuple[str, str] | None:
    """解析文件名尾部显式版本，返回规范家族名和业务版本标签。"""
    normalized = unicodedata.normalize("NFKC", str(filename or "")).strip()
    stem = _KNOWN_EXTENSION.sub("", PurePath(normalized).name)
    match = _DECIMAL_VERSION.search(stem) or _INTEGER_VERSION.search(stem)
    if not match:
        return None
    family = re.sub(r"(?:[-_\s]*v|[-_\s]*版本|[-_\s]+)$", "", stem[: match.start()], flags=re.IGNORECASE).strip()
    if not family:
        return None
    return unicodedata.normalize("NFKC", family).casefold(), match.group("version")


def version_key(value: str | float | None) -> tuple[int, ...] | None:
    """把业务版本号转成分段整数键；拒绝不完整或带预发布后缀的标签。"""
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = re.sub(r"^(?:v|版本)\s*", "", normalized, flags=re.IGNORECASE)
    if not normalized or not re.fullmatch(r"\d+(?:\.\d+)*", normalized):
        return None
    return tuple(int(part) for part in normalized.split("."))


def same_version(left: str | float | None, right: str | float | None) -> bool:
    left_key = version_key(left)
    return left_key is not None and left_key == version_key(right)
