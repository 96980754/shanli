from __future__ import annotations

import io
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import yuxi.knowledge.parser.legacy_office as legacy_office
import yuxi.knowledge.parser.unified as parser_unified
from PIL import Image

from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.parser.legacy_office import (
    LEGACY_OFFICE_FORMATS,
    OLE_COMPOUND_FILE_SIGNATURE,
    LegacyOfficeConversionError,
    LegacyOfficeConversionResult,
    LegacyOfficeConverter,
)
from yuxi.knowledge.parser.unified import (
    DocumentBlock,
    MarkdownParseResult,
    Parser,
    get_enabled_file_extensions,
    get_file_format_capabilities,
    validate_document_bytes,
)


def _ooxml_bytes(suffix: str) -> bytes:
    required_member = {
        ".docx": "word/document.xml",
        ".xlsx": "xl/workbook.xml",
        ".pptx": "ppt/presentation.xml",
    }[suffix]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(required_member, "<document/>")
    return buffer.getvalue()


def _image_bytes(image_format: str, *, frames: int = 1, size: tuple[int, int] = (48, 32)) -> bytes:
    images = [Image.new("RGB", size, (255, 255 - index, 255)) for index in range(frames)]
    buffer = io.BytesIO()
    save_kwargs = {"save_all": True, "append_images": images[1:], "duration": 50, "loop": 0} if frames > 1 else {}
    images[0].save(buffer, format=image_format, **save_kwargs)
    return buffer.getvalue()


def _conversion_run_factory(
    calls: list[dict],
    *,
    output_suffix: str | None = None,
    output_content: bytes | None = None,
    returncode: int = 0,
):
    def fake_run(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, stdout=b"LibreOffice 24.2.7.2", stderr=b"")
        calls.append({"command": command, "kwargs": kwargs})
        if returncode:
            return subprocess.CompletedProcess(command, returncode, stdout=b"", stderr=b"conversion failed")
        outdir = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        suffix = output_suffix or LEGACY_OFFICE_FORMATS[source.suffix.lower()].normalized_suffix
        target = outdir / f"{source.stem}{suffix}"
        target.write_bytes(output_content if output_content is not None else _ooxml_bytes(suffix))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    return fake_run


@pytest.mark.parametrize("suffix", [".doc", ".xls", ".ppt"])
def test_legacy_office_validation_accepts_ole_container_when_converter_available(
    suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))

    validate_document_bytes(
        f"legacy{suffix}",
        OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload",
        params={"legacy_office_enabled": True},
    )


@pytest.mark.parametrize("suffix", [".doc", ".xls", ".ppt"])
def test_legacy_office_validation_rejects_forged_extension(
    suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))

    with pytest.raises(ValueError, match="OLE"):
        validate_document_bytes(
            f"forged{suffix}",
            b"not an OLE document",
            params={"legacy_office_enabled": True},
        )


def test_legacy_office_validation_reports_converter_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: None))

    with pytest.raises(ValueError, match="LibreOffice"):
        validate_document_bytes(
            "legacy.doc",
            OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload",
            params={"legacy_office_enabled": True},
        )


@pytest.mark.parametrize(
    ("source_suffix", "normalized_suffix"), [(".doc", ".docx"), (".xls", ".xlsx"), (".ppt", ".pptx")]
)
def test_legacy_office_converter_uses_safe_isolated_profile_and_cleans_temp_files(
    tmp_path: Path,
    source_suffix: str,
    normalized_suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / f"legacy{source_suffix}"
    source.write_bytes(OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload")
    calls: list[dict] = []
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))
    monkeypatch.setattr(legacy_office.subprocess, "run", _conversion_run_factory(calls))

    result = LegacyOfficeConverter({"legacy_office_enabled": True}).convert(source)

    assert result.normalized_suffix == normalized_suffix
    assert result.content == _ooxml_bytes(normalized_suffix)
    assert result.metadata["original_format"] == source_suffix.removeprefix(".")
    assert result.metadata["normalized_format"] == normalized_suffix.removeprefix(".")
    conversion_call = calls[0]
    assert isinstance(conversion_call["command"], list)
    assert conversion_call["kwargs"]["shell"] is False
    assert conversion_call["kwargs"]["timeout"] > 0
    profile_argument = next(item for item in conversion_call["command"] if item.startswith("-env:UserInstallation="))
    assert profile_argument.startswith("-env:UserInstallation=file:")
    temp_dir = Path(conversion_call["command"][conversion_call["command"].index("--outdir") + 1])
    assert not temp_dir.exists()


def test_concurrent_legacy_office_conversions_use_distinct_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = []
    for index in range(2):
        source = tmp_path / f"legacy-{index}.doc"
        source.write_bytes(OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload")
        sources.append(source)
    calls: list[dict] = []
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))
    monkeypatch.setattr(legacy_office.subprocess, "run", _conversion_run_factory(calls))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(LegacyOfficeConverter({"legacy_office_enabled": True}).convert, sources))

    assert all(result.normalized_suffix == ".docx" for result in results)
    profile_arguments = [
        next(item for item in call["command"] if item.startswith("-env:UserInstallation=")) for call in calls
    ]
    assert len(set(profile_arguments)) == 2


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("timeout", "conversion_timeout"),
        ("nonzero", "conversion_failed"),
        ("missing", "conversion_failed"),
        ("empty", "invalid_converted_output"),
        ("wrong_type", "invalid_converted_output"),
    ],
)
def test_legacy_office_conversion_failures_are_classified_and_sanitized(
    tmp_path: Path,
    failure: str,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "private-source.doc"
    source.write_bytes(OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload")
    calls: list[dict] = []
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))
    if failure == "timeout":

        def fake_run(command, **_kwargs):
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"LibreOffice 24.2", stderr=b"")
            raise subprocess.TimeoutExpired(command, 1)

    elif failure == "nonzero":
        fake_run = _conversion_run_factory(calls, returncode=1)
    elif failure == "missing":

        def fake_run(command, **_kwargs):
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"LibreOffice 24.2", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    elif failure == "empty":
        fake_run = _conversion_run_factory(calls, output_content=b"")
    else:
        fake_run = _conversion_run_factory(calls, output_content=_ooxml_bytes(".xlsx"))
    monkeypatch.setattr(legacy_office.subprocess, "run", fake_run)

    with pytest.raises(LegacyOfficeConversionError) as exc_info:
        LegacyOfficeConverter({"legacy_office_enabled": True, "legacy_office_timeout_seconds": 1}).convert(source)

    assert exc_info.value.code == expected_code
    assert str(source) not in str(exc_info.value)
    assert "conversion failed" not in str(exc_info.value)


@pytest.mark.parametrize(("suffix", "image_format"), [(".gif", "GIF"), (".webp", "WEBP")])
def test_gif_and_webp_signature_validation_accepts_real_images(
    suffix: str,
    image_format: str,
) -> None:
    validate_document_bytes(f"image{suffix}", _image_bytes(image_format))


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("fake.gif", b"GIF89a-not-an-image"),
        ("fake.webp", b"RIFF\x10\x00\x00\x00WEBPnot-an-image"),
        ("renamed.gif", b"RIFF\x10\x00\x00\x00WEBPnot-a-gif"),
        ("renamed.webp", b"GIF89anot-a-webp"),
    ],
)
def test_gif_and_webp_validation_rejects_corruption_or_extension_mismatch(
    filename: str,
    content: bytes,
) -> None:
    with pytest.raises(ValueError):
        validate_document_bytes(filename, content)


def test_gif_validation_rejects_excessive_dimensions_and_frame_count() -> None:
    oversized = _image_bytes("GIF", size=(11, 11))
    animated = _image_bytes("GIF", frames=3)

    with pytest.raises(ValueError, match="像素|尺寸"):
        validate_document_bytes("large.gif", oversized, params={"ocr_max_image_pixels": 100})
    with pytest.raises(ValueError, match="帧"):
        validate_document_bytes("many.gif", animated, params={"ocr_max_image_frames": 2})


@pytest.mark.asyncio
async def test_animated_gif_uses_first_frame_for_ocr_and_cleans_normalized_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "animated.gif"
    source.write_bytes(_image_bytes("GIF", frames=2))
    observed_path: Path | None = None

    async def fake_ocr_route(file_path, params=None, page_number=None):
        nonlocal observed_path
        observed_path = Path(file_path)
        assert observed_path.suffix == ".png"
        assert observed_path.exists()
        with Image.open(observed_path) as image:
            assert image.format == "PNG"
            assert image.mode == "RGB"
        return SimpleNamespace(
            markdown="GIF first frame OCR marker 48217",
            parser_name="rapid_ocr",
            parser_version="test",
            warnings=[],
            attempts=[{"provider": "rapid_ocr", "status": "accepted"}],
            quality={"accepted": True, "score": 1.0},
            page_number=page_number,
        )

    monkeypatch.setattr(parser_unified, "run_ocr_fallback", fake_ocr_route)

    result = await Parser.aparse_result(str(source))
    metadata = result.to_metadata()

    assert observed_path is not None
    assert not observed_path.exists()
    assert result.parser_name == "rapid_ocr"
    assert result.file_ext == ".gif"
    assert metadata["original_format"] == "gif"
    assert metadata["normalized_format"] == "png"
    assert metadata["frame_count"] == 2
    assert metadata["selected_frames"] == [0]
    assert metadata["animation_ignored"] is True
    assert result.blocks[0].page_number == 1
    assert result.blocks[0].parser_name == "rapid_ocr"


@pytest.mark.asyncio
async def test_static_webp_preserves_original_format_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "static.webp"
    source.write_bytes(_image_bytes("WEBP"))

    async def fake_ocr_route(_file_path, params=None, page_number=None):
        return SimpleNamespace(
            markdown="WebP OCR marker 59328",
            parser_name="rapid_ocr",
            parser_version="test",
            warnings=[],
            attempts=[{"provider": "rapid_ocr", "status": "accepted"}],
            quality={"accepted": True, "score": 1.0},
            page_number=page_number,
        )

    monkeypatch.setattr(parser_unified, "run_ocr_fallback", fake_ocr_route)

    result = await Parser.aparse_result(str(source))
    metadata = result.to_metadata()

    assert metadata["original_format"] == "webp"
    assert metadata["normalized_format"] == "png"
    assert metadata["frame_count"] == 1
    assert metadata["selected_frames"] == [0]
    assert metadata["animation_ignored"] is False


def test_legacy_office_parser_reuses_normalized_parser_and_records_conversion_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload")
    observed_suffixes: list[str] = []

    def fake_convert(_self, path: Path) -> LegacyOfficeConversionResult:
        assert path == source
        return LegacyOfficeConversionResult(
            content=_ooxml_bytes(".docx"),
            normalized_suffix=".docx",
            metadata={
                "original_format": "doc",
                "normalized_format": "docx",
                "conversion_required": True,
                "converter_name": "libreoffice",
                "converter_version": "24.2",
                "conversion_duration_ms": 7,
                "conversion_warnings": [],
            },
        )

    def fake_parse_docx(path: Path, params=None) -> MarkdownParseResult:
        del params
        observed_suffixes.append(path.suffix)
        block = DocumentBlock(block_type="paragraph", order=0, text="Legacy DOC marker", markdown="Legacy DOC marker")
        return MarkdownParseResult(
            markdown="Legacy DOC marker",
            document_title=path.stem,
            parser_name="python-docx",
            parser_version="test",
            blocks=[block],
            file_ext=".docx",
        )

    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))
    monkeypatch.setattr(LegacyOfficeConverter, "convert", fake_convert)
    monkeypatch.setattr(parser_unified, "_parse_docx", fake_parse_docx)

    result = Parser.parse_result(str(source), params={"legacy_office_enabled": True})
    metadata = result.to_metadata()

    assert observed_suffixes == [".docx"]
    assert result.file_ext == ".doc"
    assert result.document_title == "legacy"
    assert result.parser_name == "python-docx"
    assert metadata["original_format"] == "doc"
    assert metadata["normalized_format"] == "docx"
    assert metadata["conversion_required"] is True
    assert metadata["converter_name"] == "libreoffice"
    assert metadata["converter_version"] == "24.2"
    assert metadata["conversion_duration_ms"] == 7


def test_legacy_office_empty_normalized_parse_result_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "empty.doc"
    source.write_bytes(OLE_COMPOUND_FILE_SIGNATURE + b"valid compound payload")
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: "soffice"))
    monkeypatch.setattr(
        LegacyOfficeConverter,
        "convert",
        lambda _self, _path: LegacyOfficeConversionResult(
            content=_ooxml_bytes(".docx"),
            normalized_suffix=".docx",
            metadata={"original_format": "doc", "normalized_format": "docx"},
        ),
    )
    monkeypatch.setattr(
        parser_unified,
        "_parse_docx",
        lambda *_args, **_kwargs: MarkdownParseResult(
            markdown="",
            document_title=None,
            parser_name="python-docx",
            parser_version="test",
            blocks=[],
            file_ext=".docx",
        ),
    )

    with pytest.raises(ValueError, match="有效文本"):
        Parser.parse_result(str(source), params={"legacy_office_enabled": True})


def test_supported_capabilities_hide_legacy_office_when_converter_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LegacyOfficeConverter, "resolve_binary", staticmethod(lambda _configured=None: None))

    enabled = get_enabled_file_extensions()
    capabilities = {item["extension"]: item for item in get_file_format_capabilities()}

    assert ".gif" in enabled
    assert ".webp" in enabled
    assert ".doc" not in enabled
    assert capabilities[".doc"]["availability"] == "converter_unavailable"
    assert "LibreOffice" in capabilities[".doc"]["reason"]


def test_chunk_source_metadata_retains_original_and_normalized_formats() -> None:
    chunks = [{"content": "Legacy DOC marker", "start_char_pos": 0, "end_char_pos": 17}]
    parse_metadata = {
        "parser_name": "python-docx",
        "parser_version": "test",
        "original_format": "doc",
        "normalized_format": "docx",
        "blocks": [
            {
                "block_type": "paragraph",
                "order": 0,
                "text": "Legacy DOC marker",
                "start_char_pos": 0,
                "end_char_pos": 17,
            }
        ],
    }

    MilvusKB._attach_source_metadata(chunks, parse_metadata)

    assert chunks[0]["source_metadata"]["original_format"] == "doc"
    assert chunks[0]["source_metadata"]["normalized_format"] == "docx"
    assert chunks[0]["source_metadata"]["parser_name"] == "python-docx"
