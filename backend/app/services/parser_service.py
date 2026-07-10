from __future__ import annotations

from pathlib import Path
from typing import Any
import re
from zipfile import ZipFile
from xml.etree import ElementTree

try:
    from unstructured.partition.auto import partition
except ImportError:
    partition = None


def parse_file_to_elements(file_path: Path) -> list[dict[str, Any]]:
    suffix = file_path.suffix.lower()
    if suffix == ".txt":
        return [
            {
                "type": "text",
                "content": file_path.read_text(encoding="utf-8"),
                "source_name": file_path.name,
            }
        ]
    if suffix == ".docx":
        return _parse_unstructured(file_path) or _parse_docx(file_path)
    if suffix == ".pdf":
        return _parse_unstructured(file_path) or _parse_pdf(file_path)
    raise ValueError(f"Unsupported file type for parser skeleton: {suffix}")


def _parse_unstructured(file_path: Path) -> list[dict[str, Any]]:
    if partition is None:
        return []
    try:
        raw_elements = partition(filename=str(file_path))
    except Exception:
        return []

    elements = []
    for element in raw_elements:
        text = str(getattr(element, "text", "")).strip()
        if not text:
            continue
        elements.append(
            {
                "type": getattr(element, "category", element.__class__.__name__),
                "content": text,
                "source_name": file_path.name,
            }
        )
    return elements


def _parse_docx(file_path: Path) -> list[dict[str, Any]]:
    with ZipFile(file_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    elements = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            elements.append({"type": "text", "content": text, "source_name": file_path.name})
    return elements


def _parse_pdf(file_path: Path) -> list[dict[str, Any]]:
    raw = file_path.read_bytes().decode("latin-1", errors="ignore")
    texts = [text.strip() for text in re.findall(r"\(([^()]*)\)\s*Tj", raw) if text.strip()]
    return [{"type": "text", "content": text, "source_name": file_path.name} for text in texts]
