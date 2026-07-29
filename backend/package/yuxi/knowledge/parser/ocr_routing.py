"""Deterministic OCR quality assessment and bounded provider fallback routing."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PIL import Image

from yuxi.knowledge.parser.factory import DocumentProcessorFactory
from yuxi.knowledge.utils.kb_utils import sanitize_processing_error

BASE_OCR_PROVIDERS = ("rapid_ocr", "pp_structure_v3_ocr")
ADVANCED_OCR_PROVIDERS = ("paddleocr_vl_1_6", "mineru_ocr", "mineru_official")
PROVIDER_STAGES = {
    "rapid_ocr": "ocr_processing",
    "pp_structure_v3_ocr": "structure_processing",
    "paddleocr_vl_1_6": "vl_processing",
    "mineru_ocr": "vl_processing",
    "mineru_official": "vl_processing",
}
PROVIDER_PACKAGES = {
    "rapid_ocr": "rapidocr",
}


def _int_setting(params: dict[str, Any], key: str, env_key: str, default: int) -> int:
    value = params.get(key)
    if value is None:
        value = os.getenv(env_key, str(default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_setting(params: dict[str, Any], key: str, env_key: str, default: float) -> float:
    value = params.get(key)
    if value is None:
        value = os.getenv(env_key, str(default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class OCRRoutingPolicy:
    """Centralized deterministic thresholds and safety limits for OCR routing."""

    native_pdf_min_valid_characters: int = 8
    ocr_min_valid_characters: int = 8
    accepted_score: float = 0.55
    min_valid_character_ratio: float = 0.35
    max_repeated_character_ratio: float = 0.45
    max_garbled_character_ratio: float = 0.20
    max_image_pixels: int = 40_000_000
    max_image_dimension: int = 20_000
    max_image_frames: int = 500
    max_image_decode_seconds: float = 30.0
    max_legacy_image_bytes: int = 50 * 1024 * 1024
    max_normalized_image_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 200
    max_pdf_page_pixels: int = 25_000_000
    pdf_render_scale: float = 2.0
    max_ocr_seconds: float = 180.0
    provider_health_timeout_seconds: float = 8.0
    max_provider_attempts: int = 3

    @classmethod
    def from_params(cls, params: dict[str, Any] | None = None) -> OCRRoutingPolicy:
        values = params or {}
        return cls(
            native_pdf_min_valid_characters=max(
                1,
                _int_setting(values, "ocr_native_pdf_min_chars", "OCR_NATIVE_PDF_MIN_CHARS", 8),
            ),
            ocr_min_valid_characters=max(
                1,
                _int_setting(values, "ocr_min_valid_chars", "OCR_MIN_VALID_CHARS", 8),
            ),
            accepted_score=min(
                1.0,
                max(0.0, _float_setting(values, "ocr_accepted_score", "OCR_ACCEPTED_SCORE", 0.55)),
            ),
            min_valid_character_ratio=min(
                1.0,
                max(
                    0.0,
                    _float_setting(
                        values,
                        "ocr_min_valid_character_ratio",
                        "OCR_MIN_VALID_CHARACTER_RATIO",
                        0.35,
                    ),
                ),
            ),
            max_repeated_character_ratio=min(
                1.0,
                max(
                    0.0,
                    _float_setting(
                        values,
                        "ocr_max_repeated_character_ratio",
                        "OCR_MAX_REPEATED_CHARACTER_RATIO",
                        0.45,
                    ),
                ),
            ),
            max_garbled_character_ratio=min(
                1.0,
                max(
                    0.0,
                    _float_setting(
                        values,
                        "ocr_max_garbled_character_ratio",
                        "OCR_MAX_GARBLED_CHARACTER_RATIO",
                        0.20,
                    ),
                ),
            ),
            max_image_pixels=max(
                1,
                _int_setting(values, "ocr_max_image_pixels", "OCR_MAX_IMAGE_PIXELS", 40_000_000),
            ),
            max_image_dimension=max(
                1,
                _int_setting(values, "ocr_max_image_dimension", "OCR_MAX_IMAGE_DIMENSION", 20_000),
            ),
            max_image_frames=max(
                1,
                _int_setting(values, "ocr_max_image_frames", "OCR_MAX_IMAGE_FRAMES", 500),
            ),
            max_image_decode_seconds=max(
                1.0,
                _float_setting(
                    values,
                    "ocr_max_image_decode_seconds",
                    "OCR_MAX_IMAGE_DECODE_SECONDS",
                    30.0,
                ),
            ),
            max_legacy_image_bytes=max(
                1,
                _int_setting(
                    values,
                    "ocr_max_legacy_image_bytes",
                    "OCR_MAX_LEGACY_IMAGE_BYTES",
                    50 * 1024 * 1024,
                ),
            ),
            max_normalized_image_bytes=max(
                1,
                _int_setting(
                    values,
                    "ocr_max_normalized_image_bytes",
                    "OCR_MAX_NORMALIZED_IMAGE_BYTES",
                    50 * 1024 * 1024,
                ),
            ),
            max_pdf_pages=max(
                1,
                _int_setting(values, "ocr_max_pdf_pages", "OCR_MAX_PDF_PAGES", 200),
            ),
            max_pdf_page_pixels=max(
                1,
                _int_setting(
                    values,
                    "ocr_max_pdf_page_pixels",
                    "OCR_MAX_PDF_PAGE_PIXELS",
                    25_000_000,
                ),
            ),
            pdf_render_scale=max(
                0.5,
                _float_setting(values, "ocr_pdf_render_scale", "OCR_PDF_RENDER_SCALE", 2.0),
            ),
            max_ocr_seconds=max(
                1.0,
                _float_setting(values, "ocr_max_seconds", "OCR_MAX_SECONDS", 180.0),
            ),
            provider_health_timeout_seconds=max(
                1.0,
                _float_setting(
                    values,
                    "ocr_provider_health_timeout_seconds",
                    "OCR_PROVIDER_HEALTH_TIMEOUT_SECONDS",
                    8.0,
                ),
            ),
            max_provider_attempts=min(
                3,
                max(
                    1,
                    _int_setting(
                        values,
                        "ocr_max_provider_attempts",
                        "OCR_MAX_PROVIDER_ATTEMPTS",
                        3,
                    ),
                ),
            ),
        )

    def public_metadata(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OCRQualityResult:
    accepted: bool
    score: float
    reasons: list[str]
    valid_characters: int
    valid_character_ratio: float
    repeated_character_ratio: float
    garbled_character_ratio: float
    empty_line_ratio: float
    table_valid_cells: int
    page_coverage: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OCRRouteResult:
    markdown: str
    parser_name: str
    parser_version: str
    warnings: list[str]
    attempts: list[dict[str, Any]]
    quality: dict[str, Any]
    page_number: int | None = None


class OCRRoutingError(ValueError):
    """OCR failure carrying safe attempt metadata for persistence."""

    def __init__(self, message: str, *, attempts: list[dict[str, Any]], warnings: list[str]):
        super().__init__(message)
        self.parse_metadata = {
            "attempts": attempts,
            "warnings": warnings,
            "quality": {
                "accepted": False,
                "score": 0.0,
                "reasons": ["all_providers_failed"],
            },
        }


def _maximum_repeated_ratio(text: str) -> float:
    compact = "".join(character for character in text if not character.isspace())
    if not compact:
        return 0.0
    maximum_run = 1
    current_run = 1
    for previous, current in zip(compact, compact[1:], strict=False):
        if current == previous:
            current_run += 1
            maximum_run = max(maximum_run, current_run)
        else:
            current_run = 1
    return maximum_run / len(compact)


def _table_valid_cells(markdown: str) -> int:
    count = 0
    for line in markdown.splitlines():
        if "|" not in line:
            continue
        for cell in line.strip().strip("|").split("|"):
            value = cell.strip()
            if not value or re.fullmatch(r":?-{3,}:?", value):
                continue
            if re.search(r"[A-Za-z0-9\u3400-\u9fff]", value):
                count += 1
    return count


def assess_text_quality(
    text: str,
    *,
    policy: OCRRoutingPolicy | None = None,
    min_valid_characters: int | None = None,
    page_coverage: float | None = None,
) -> OCRQualityResult:
    """Score OCR output using explicit, deterministic text statistics."""
    policy = policy or OCRRoutingPolicy()
    minimum = max(1, min_valid_characters or policy.ocr_min_valid_characters)
    normalized = str(text or "")
    non_whitespace = [character for character in normalized if not character.isspace()]
    valid_characters = re.findall(r"[A-Za-z0-9\u3400-\u9fff]", normalized)
    valid_ratio = len(valid_characters) / max(len(non_whitespace), 1)
    garbled_count = sum(
        character == "\ufffd"
        or (unicodedata.category(character) in {"Cc", "Cs"} and character not in {"\n", "\r", "\t"})
        for character in normalized
    )
    garbled_ratio = garbled_count / max(len(non_whitespace), 1)
    repeated_ratio = _maximum_repeated_ratio(normalized)
    lines = normalized.splitlines()
    empty_line_ratio = sum(not line.strip() for line in lines) / max(len(lines), 1)
    table_cells = _table_valid_cells(normalized)

    semantic_amount = min(len(valid_characters) / max(minimum * 2, 1), 1.0)
    coverage_component = 1.0 if page_coverage is None else min(max(page_coverage, 0.0), 1.0)
    score = (
        semantic_amount * 0.45
        + valid_ratio * 0.25
        + (1.0 - min(garbled_ratio, 1.0)) * 0.10
        + (1.0 - min(repeated_ratio, 1.0)) * 0.10
        + (1.0 - min(empty_line_ratio, 1.0)) * 0.05
        + coverage_component * 0.05
    )
    score = round(min(max(score, 0.0), 1.0), 4)

    reasons: list[str] = []
    if len(valid_characters) < minimum:
        reasons.append("insufficient_valid_characters")
    if valid_ratio < policy.min_valid_character_ratio:
        reasons.append("low_valid_character_ratio")
    if repeated_ratio > policy.max_repeated_character_ratio:
        reasons.append("excessive_repetition")
    if garbled_ratio > policy.max_garbled_character_ratio:
        reasons.append("excessive_garbled_characters")
    if score < policy.accepted_score:
        reasons.append("score_below_threshold")

    return OCRQualityResult(
        accepted=not reasons,
        score=score,
        reasons=reasons,
        valid_characters=len(valid_characters),
        valid_character_ratio=round(valid_ratio, 4),
        repeated_character_ratio=round(repeated_ratio, 4),
        garbled_character_ratio=round(garbled_ratio, 4),
        empty_line_ratio=round(empty_line_ratio, 4),
        table_valid_cells=table_cells,
        page_coverage=round(page_coverage, 4) if page_coverage is not None else None,
    )


def _line_groups(values: Any) -> int:
    indexes = [index for index, value in enumerate(values) if bool(value)]
    if not indexes:
        return 0
    groups = 1
    for previous, current in zip(indexes, indexes[1:], strict=False):
        if current > previous + 1:
            groups += 1
    return groups


def looks_like_structured_layout(file_path: str | Path) -> bool:
    """Detect obvious table ruling lines; it is deliberately conservative."""
    try:
        import numpy as np

        with Image.open(file_path) as image:
            grayscale = image.convert("L")
            grayscale.thumbnail((1200, 1200))
            pixels = np.asarray(grayscale)
        dark = pixels < 96
        horizontal_lines = _line_groups((dark.mean(axis=1) >= 0.50).tolist())
        vertical_lines = _line_groups((dark.mean(axis=0) >= 0.50).tolist())
        return horizontal_lines >= 3 and vertical_lines >= 2
    except Exception:
        return False


def _provider_order(params: dict[str, Any]) -> list[str]:
    providers = list(BASE_OCR_PROVIDERS)
    advanced = str(
        params.get("ocr_advanced_provider") or os.getenv("OCR_ADVANCED_PROVIDER") or params.get("ocr_engine") or ""
    ).strip()
    if advanced in ADVANCED_OCR_PROVIDERS:
        providers.append(advanced)
    return providers


def _provider_version(provider: str) -> str:
    package = PROVIDER_PACKAGES.get(provider)
    if not package:
        return "remote"
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


async def notify_processing_stage(params: dict[str, Any], stage: str, progress: int) -> None:
    callback = params.get("_stage_callback")
    if not callable(callback):
        return
    result = callback(stage, max(0, min(int(progress), 100)))
    if inspect.isawaitable(result):
        await result


def _public_processor_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if not str(key).startswith("_")}


async def run_ocr_fallback(
    file_path: str | Path,
    *,
    params: dict[str, Any] | None = None,
    page_number: int | None = None,
) -> OCRRouteResult:
    """Run at most three configured providers with health checks and quality gates."""
    options = dict(params or {})
    policy = OCRRoutingPolicy.from_params(options)
    providers = _provider_order(options)[: policy.max_provider_attempts]
    attempts: list[dict[str, Any]] = []
    warnings: list[str] = []
    started = time.monotonic()
    deadline = float(options.get("_ocr_deadline_monotonic") or (started + policy.max_ocr_seconds))
    best_result: OCRRouteResult | None = None
    structured_layout = bool(options.get("_complex_layout")) or looks_like_structured_layout(file_path)

    for provider in providers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            attempts.append(
                {
                    "provider": provider,
                    "stage": PROVIDER_STAGES[provider],
                    "status": "skipped",
                    "duration_ms": 0,
                    "failure_reason": "ocr_time_budget_exhausted",
                }
            )
            warnings.append(f"{provider} skipped: OCR time budget exhausted")
            continue

        stage = PROVIDER_STAGES[provider]
        progress = {"ocr_processing": 35, "structure_processing": 45, "vl_processing": 50}[stage]
        await notify_processing_stage(options, stage, progress)
        attempt_started = time.monotonic()
        try:
            processor = DocumentProcessorFactory.get_processor(provider)
            health = await asyncio.wait_for(
                asyncio.to_thread(processor.check_health),
                timeout=min(policy.provider_health_timeout_seconds, remaining),
            )
            health_status = str((health or {}).get("status") or "error")
            if health_status not in {"healthy", "configured"}:
                reason = sanitize_processing_error((health or {}).get("message") or health_status)
                attempts.append(
                    {
                        "provider": provider,
                        "stage": stage,
                        "status": "skipped",
                        "duration_ms": round((time.monotonic() - attempt_started) * 1000),
                        "failure_reason": reason,
                    }
                )
                warnings.append(f"{provider} skipped: {reason}")
                continue

            remaining = max(deadline - time.monotonic(), 0.001)
            markdown = await asyncio.wait_for(
                asyncio.to_thread(
                    processor.process_file,
                    str(file_path),
                    _public_processor_params(options),
                ),
                timeout=remaining,
            )
            quality = assess_text_quality(str(markdown or ""), policy=policy)
            attempt = {
                "provider": provider,
                "stage": stage,
                "status": "accepted" if quality.accepted else "rejected",
                "duration_ms": round((time.monotonic() - attempt_started) * 1000),
                "quality": quality.to_dict(),
            }
            attempts.append(attempt)
            if not quality.accepted:
                continue

            result = OCRRouteResult(
                markdown=str(markdown),
                parser_name=provider,
                parser_version=_provider_version(provider),
                warnings=list(warnings),
                attempts=attempts,
                quality=quality.to_dict(),
                page_number=page_number,
            )
            if provider == "rapid_ocr" and structured_layout and "pp_structure_v3_ocr" in providers:
                attempt["status"] = "deferred"
                attempt["fallback_reason"] = "structured_layout_detected"
                warnings.append("RapidOCR text accepted, but structured layout triggered PP-Structure fallback")
                result.warnings = list(warnings)
                best_result = result
                continue
            return result
        except TimeoutError:
            reason = "provider_timeout"
        except Exception as exc:  # noqa: BLE001
            reason = sanitize_processing_error(exc)

        attempts.append(
            {
                "provider": provider,
                "stage": stage,
                "status": "error",
                "duration_ms": round((time.monotonic() - attempt_started) * 1000),
                "failure_reason": reason,
            }
        )
        warnings.append(f"{provider} failed: {reason}")

    if best_result is not None:
        best_result.warnings = list(warnings)
        return best_result

    raise OCRRoutingError(
        "OCR 未提取到达到质量阈值的有效文本",
        attempts=attempts,
        warnings=warnings,
    )
