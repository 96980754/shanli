from pathlib import Path
from zipfile import ZipFile

import app.services.parser_service as parser_service
from app.services.parser_service import parse_file_to_elements


def test_parse_txt_file_returns_single_text_element(tmp_path: Path):
    file_path = tmp_path / "faq.txt"
    file_path.write_text("SOS报警可以在设置里关闭。", encoding="utf-8")

    elements = parse_file_to_elements(file_path)

    assert len(elements) == 1
    assert elements[0]["type"] == "text"
    assert "SOS报警" in elements[0]["content"]
    assert elements[0]["source_name"] == "faq.txt"


def test_parse_docx_file_returns_paragraph_text_elements(tmp_path: Path):
    file_path = tmp_path / "manual.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>MCSTARS 产品白皮书</w:t></w:r></w:p>
    <w:p><w:r><w:t>MCSTARS 支持统一调度。</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with ZipFile(file_path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    elements = parse_file_to_elements(file_path)

    assert [element["content"] for element in elements] == [
        "MCSTARS 产品白皮书",
        "MCSTARS 支持统一调度。",
    ]
    assert all(element["type"] == "text" for element in elements)
    assert all(element["source_name"] == "manual.docx" for element in elements)


def test_parser_routes_docx_through_unstructured_adapter(tmp_path: Path, monkeypatch):
    file_path = tmp_path / "manual.docx"
    with ZipFile(file_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", "")

    def fake_partition(filename: str):
        assert filename == str(file_path)
        return [
            type("Element", (), {"category": "Title", "text": "MCSTARS 产品白皮书"})(),
            type("Element", (), {"category": "NarrativeText", "text": "MCSTARS 支持统一调度。"})(),
        ]

    monkeypatch.setattr(parser_service, "partition", fake_partition)

    elements = parse_file_to_elements(file_path)

    assert elements == [
        {"type": "Title", "content": "MCSTARS 产品白皮书", "source_name": "manual.docx"},
        {"type": "NarrativeText", "content": "MCSTARS 支持统一调度。", "source_name": "manual.docx"},
    ]


def test_parse_pdf_file_returns_text_elements_from_simple_text_stream(tmp_path: Path):
    file_path = tmp_path / "manual.pdf"
    file_path.write_bytes(b"%PDF-1.4\nBT (MCSTARS PDF guide) Tj ET\n%%EOF")

    elements = parse_file_to_elements(file_path)

    assert len(elements) == 1
    assert elements[0]["type"] == "text"
    assert elements[0]["content"] == "MCSTARS PDF guide"
    assert elements[0]["source_name"] == "manual.pdf"
