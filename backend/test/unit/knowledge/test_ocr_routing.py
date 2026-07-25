from __future__ import annotations

import io
import time
from pathlib import Path

import fitz
import pytest
import yuxi.knowledge.parser.unified as parser_unified
from PIL import Image, ImageDraw

from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.knowledge.parser.ocr_routing import (
    OCRRouteResult,
    OCRRoutingError,
    OCRRoutingPolicy,
    assess_text_quality,
    looks_like_structured_layout,
    run_ocr_fallback,
)
from yuxi.knowledge.parser.unified import Parser


class FakeProcessor:
    def __init__(self, name: str, *, health: str = "healthy", output: str = "", error: Exception | None = None):
        self.name = name
        self.health = health
        self.output = output
        self.error = error
        self.health_calls = 0
        self.process_calls = 0

    def check_health(self) -> dict:
        self.health_calls += 1
        return {"status": self.health, "message": f"{self.name} health"}

    def process_file(self, _file_path: str, _params: dict | None = None) -> str:
        self.process_calls += 1
        if self.error:
            raise self.error
        return self.output


def _install_processors(monkeypatch: pytest.MonkeyPatch, processors: dict[str, FakeProcessor]) -> None:
    monkeypatch.setattr(
        DocumentProcessorFactory,
        "get_processor",
        classmethod(lambda _cls, processor_type, **_kwargs: processors[processor_type]),
    )


def _build_pdf(path: Path, page_texts: list[str]) -> None:
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _add_scanned_page(document: fitz.Document) -> None:
    image = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 100), "Scanned image content 12345", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    page = document.new_page()
    page.insert_image(page.rect, stream=buffer.getvalue())


def _build_scanned_pdf(path: Path, *, native_first_page: str | None = None) -> None:
    document = fitz.open()
    if native_first_page:
        page = document.new_page()
        page.insert_text((72, 72), native_first_page)
    _add_scanned_page(document)
    document.save(path)
    document.close()


def _accepted_ocr_result(text: str, page_number: int = 1) -> OCRRouteResult:
    policy = OCRRoutingPolicy()
    quality = assess_text_quality(text, policy=policy).to_dict()
    return OCRRouteResult(
        markdown=text,
        parser_name="rapid_ocr",
        parser_version="test",
        warnings=[],
        attempts=[
            {
                "provider": "rapid_ocr",
                "stage": "ocr_processing",
                "status": "accepted",
                "duration_ms": 1,
                "quality": quality,
            }
        ],
        quality=quality,
        page_number=page_number,
    )


def test_quality_rules_accept_semantic_text_and_reject_placeholders() -> None:
    policy = OCRRoutingPolicy(ocr_min_valid_characters=8)

    accepted = assess_text_quality("Invoice 编号 A123456，total 98.50", policy=policy)
    punctuation = assess_text_quality("---- !!! ???", policy=policy)
    repeated = assess_text_quality("AAAAAAAAAAAAAAAAAAAAAAAAAAAA", policy=policy)
    garbled = assess_text_quality("\ufffd\ufffd\ufffd\ufffd text", policy=policy)

    assert accepted.accepted is True
    assert accepted.score >= policy.accepted_score
    assert punctuation.accepted is False
    assert "insufficient_valid_characters" in punctuation.reasons
    assert repeated.accepted is False
    assert "excessive_repetition" in repeated.reasons
    assert garbled.accepted is False
    assert "excessive_garbled_characters" in garbled.reasons


def test_table_quality_counts_only_nonempty_cells() -> None:
    quality = assess_text_quality("| Name | Value |\n| --- | --- |\n| alpha | 42 |")

    assert quality.table_valid_cells == 4
    assert quality.accepted is True


def test_structured_layout_detection_is_deterministic(tmp_path: Path) -> None:
    image_path = tmp_path / "table.png"
    image = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(image)
    for x in (20, 300, 580):
        draw.line((x, 20, x, 280), fill="black", width=4)
    for y in (20, 120, 220, 280):
        draw.line((20, y, 580, y), fill="black", width=4)
    image.save(image_path)

    assert looks_like_structured_layout(image_path) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "processor_output"),
    [
        ("blank.png", ""),
        ("noise.png", "|||| ???? ----"),
    ],
)
async def test_blank_and_noise_images_do_not_produce_accepted_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_name: str,
    processor_output: str,
) -> None:
    image_path = tmp_path / fixture_name
    if fixture_name == "blank.png":
        image = Image.new("RGB", (320, 160), "white")
    else:
        image = Image.effect_noise((320, 160), 100).convert("RGB")
    image.save(image_path)

    processors = {
        "rapid_ocr": FakeProcessor("rapid", output=processor_output),
        "pp_structure_v3_ocr": FakeProcessor("structure", health="unavailable"),
    }
    _install_processors(monkeypatch, processors)

    with pytest.raises(OCRRoutingError) as exc_info:
        await run_ocr_fallback(
            image_path,
        )

    assert exc_info.value.parse_metadata["quality"]["accepted"] is False
    assert [attempt["status"] for attempt in exc_info.value.parse_metadata["attempts"]] == [
        "rejected",
        "skipped",
    ]


@pytest.mark.asyncio
async def test_rapidocr_success_stops_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    processors = {
        "rapid_ocr": FakeProcessor("rapid", output="Rapid OCR extracted enough useful text 12345"),
        "pp_structure_v3_ocr": FakeProcessor("structure", output="should not run"),
    }
    _install_processors(monkeypatch, processors)

    result = await run_ocr_fallback(
        tmp_path / "image.png",
    )

    assert result.parser_name == "rapid_ocr"
    assert processors["rapid_ocr"].process_calls == 1
    assert processors["pp_structure_v3_ocr"].health_calls == 0


@pytest.mark.asyncio
async def test_low_quality_rapidocr_falls_back_to_structure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processors = {
        "rapid_ocr": FakeProcessor("rapid", output="???"),
        "pp_structure_v3_ocr": FakeProcessor(
            "structure",
            output="| Name | Value |\n| --- | --- |\n| alpha | 42 |",
        ),
    }
    _install_processors(monkeypatch, processors)

    result = await run_ocr_fallback(
        tmp_path / "image.png",
    )

    assert result.parser_name == "pp_structure_v3_ocr"
    assert [attempt["status"] for attempt in result.attempts] == ["rejected", "accepted"]


@pytest.mark.asyncio
async def test_structure_failure_uses_configured_advanced_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stages: list[tuple[str, int]] = []

    async def capture_stage(stage: str, progress: int) -> None:
        stages.append((stage, progress))

    processors = {
        "rapid_ocr": FakeProcessor("rapid", output="x"),
        "pp_structure_v3_ocr": FakeProcessor("structure", error=RuntimeError("service failed")),
        "paddleocr_vl_1_6": FakeProcessor("vl", health="configured", output="VL extracted useful content 67890"),
    }
    _install_processors(monkeypatch, processors)

    result = await run_ocr_fallback(
        tmp_path / "image.png",
        params={
            "ocr_advanced_provider": "paddleocr_vl_1_6",
            "_stage_callback": capture_stage,
        },
    )

    assert result.parser_name == "paddleocr_vl_1_6"
    assert [attempt["stage"] for attempt in result.attempts] == [
        "ocr_processing",
        "structure_processing",
        "vl_processing",
    ]
    assert [stage for stage, _progress in stages] == [
        "ocr_processing",
        "structure_processing",
        "vl_processing",
    ]
    assert all(0 <= progress <= 100 for _stage, progress in stages)


@pytest.mark.asyncio
async def test_unhealthy_optional_provider_is_skipped_and_all_failures_are_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processors = {
        "rapid_ocr": FakeProcessor("rapid", output=""),
        "pp_structure_v3_ocr": FakeProcessor("structure", health="unavailable"),
        "mineru_ocr": FakeProcessor("mineru", health="unavailable"),
    }
    _install_processors(monkeypatch, processors)

    with pytest.raises(OCRRoutingError) as exc_info:
        await run_ocr_fallback(
            tmp_path / "image.png",
            params={"ocr_advanced_provider": "mineru_ocr"},
        )

    metadata = exc_info.value.parse_metadata
    assert [attempt["status"] for attempt in metadata["attempts"]] == ["rejected", "skipped", "skipped"]
    assert metadata["quality"]["accepted"] is False
    assert "http://" not in str(metadata)


@pytest.mark.asyncio
async def test_expired_document_ocr_deadline_skips_every_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processors = {
        "rapid_ocr": FakeProcessor("rapid", output="must not run"),
        "pp_structure_v3_ocr": FakeProcessor("structure", output="must not run"),
    }
    _install_processors(monkeypatch, processors)

    with pytest.raises(OCRRoutingError) as exc_info:
        await run_ocr_fallback(
            tmp_path / "image.png",
            params={"_ocr_deadline_monotonic": time.monotonic() - 1},
        )

    assert all(processor.health_calls == 0 for processor in processors.values())
    assert all(processor.process_calls == 0 for processor in processors.values())
    assert [attempt["failure_reason"] for attempt in exc_info.value.parse_metadata["attempts"]] == [
        "ocr_time_budget_exhausted",
        "ocr_time_budget_exhausted",
    ]


def test_text_pdf_does_not_enter_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "text.pdf"
    _build_pdf(path, ["This native PDF page contains sufficient searchable text 12345."])

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("OCR must not run for a high-quality text page")

    monkeypatch.setattr(parser_unified, "run_ocr_fallback", fail_if_called)

    result = Parser.parse_result(str(path))

    assert result.parser_name == "native_pdf"
    assert result.classification["type"] == "text_pdf"
    assert result.blocks[0].page_number == 1


def test_scanned_pdf_enters_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "scan.pdf"
    _build_scanned_pdf(path)
    calls: list[int] = []

    async def fake_ocr(_path, *, params=None, page_number=None):
        calls.append(page_number)
        return _accepted_ocr_result("Scanned page OCR content 12345", page_number)

    monkeypatch.setattr(parser_unified, "run_ocr_fallback", fake_ocr)

    result = Parser.parse_result(str(path))

    assert calls == [1]
    assert result.classification["type"] == "scanned_pdf"
    assert result.blocks[0].page_number == 1
    assert result.blocks[0].text == "Scanned page OCR content 12345"


def test_mixed_pdf_routes_only_low_quality_page_to_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mixed.pdf"
    _build_scanned_pdf(path, native_first_page="Native page searchable text 12345")
    calls: list[int] = []

    async def fake_ocr(_path, *, params=None, page_number=None):
        calls.append(page_number)
        return _accepted_ocr_result("Second scanned page OCR text 67890", page_number)

    monkeypatch.setattr(parser_unified, "run_ocr_fallback", fake_ocr)

    result = Parser.parse_result(str(path))

    assert calls == [2]
    assert result.classification["type"] == "mixed_pdf"
    assert [block.page_number for block in result.blocks] == [1, 2]
    assert [block.parser_name for block in result.blocks] == ["native_pdf", "rapid_ocr"]
    assert all(block.parser_version for block in result.blocks)
    assert result.parser_name == "hybrid_pdf"
