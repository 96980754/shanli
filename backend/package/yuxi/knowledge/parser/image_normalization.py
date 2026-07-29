"""Validation and first-frame normalization for raster OCR inputs."""

from __future__ import annotations

import io
import tempfile
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError, features

from yuxi.knowledge.parser.ocr_routing import OCRRoutingPolicy

_EXPECTED_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
}


@dataclass(frozen=True, slots=True)
class ImageValidationResult:
    image_format: str
    width: int
    height: int
    frame_count: int
    animated: bool


class ImageNormalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class NormalizedOCRImage:
    path: Path
    metadata: dict[str, Any]
    _temporary_directory: tempfile.TemporaryDirectory[str]

    def cleanup(self) -> None:
        self._temporary_directory.cleanup()


def _check_signature(suffix: str, content: bytes) -> None:
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImageNormalizationError("invalid_file_signature", "PNG 文件签名不匹配")
    if suffix in {".jpg", ".jpeg"} and not (
        content.startswith(b"\xff\xd8\xff") and content.rstrip().endswith(b"\xff\xd9")
    ):
        raise ImageNormalizationError("invalid_file_signature", "JPEG 文件签名不匹配")
    if suffix == ".gif" and not (content.startswith(b"GIF87a") or content.startswith(b"GIF89a")):
        raise ImageNormalizationError("invalid_file_signature", "GIF 文件签名不匹配")
    if suffix == ".webp" and not (content.startswith(b"RIFF") and content[8:12] == b"WEBP"):
        raise ImageNormalizationError("invalid_file_signature", "WebP 文件签名不匹配")


def validate_image_bytes(
    suffix: str,
    content: bytes,
    policy: OCRRoutingPolicy,
) -> ImageValidationResult:
    normalized_suffix = suffix.lower()
    expected_format = _EXPECTED_FORMATS.get(normalized_suffix)
    if not expected_format:
        raise ImageNormalizationError("unsupported_format", "不支持的图片格式")
    if normalized_suffix == ".webp" and not features.check("webp"):
        raise ImageNormalizationError("unsupported_format", "当前 Pillow 未启用 WebP 解码能力")
    if normalized_suffix in {".gif", ".webp"} and len(content) > policy.max_legacy_image_bytes:
        raise ImageNormalizationError("image_too_large", "图片文件超过安全大小限制")
    _check_signature(normalized_suffix, content)

    started_at = time.monotonic()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected_format:
                    raise ImageNormalizationError(
                        "invalid_file_signature",
                        f"图片内容不是有效的 {expected_format} 格式",
                    )
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ImageNormalizationError("image_decode_failed", "图片尺寸无效")
                if width > policy.max_image_dimension or height > policy.max_image_dimension:
                    raise ImageNormalizationError("image_too_large", "图片宽高超过安全限制")
                if width * height > policy.max_image_pixels:
                    raise ImageNormalizationError(
                        "image_too_large",
                        f"图片像素数量超过安全限制（最多 {policy.max_image_pixels}）",
                    )
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                if frame_count > policy.max_image_frames:
                    raise ImageNormalizationError(
                        "image_too_large",
                        f"图片帧数超过安全限制（最多 {policy.max_image_frames} 帧）",
                    )
                image.seek(0)
                image.load()
    except ImageNormalizationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageNormalizationError("image_too_large", "图片像素数量超过安全限制") from exc
    except (OSError, EOFError, UnidentifiedImageError) as exc:
        raise ImageNormalizationError("image_decode_failed", f"{expected_format} 图片损坏或无法读取") from exc

    if time.monotonic() - started_at > policy.max_image_decode_seconds:
        raise ImageNormalizationError("image_decode_failed", "图片解码超时")
    return ImageValidationResult(
        image_format=expected_format,
        width=width,
        height=height,
        frame_count=frame_count,
        animated=frame_count > 1,
    )


def normalize_image_for_ocr(
    source_path: Path,
    params: dict[str, Any] | None = None,
) -> NormalizedOCRImage:
    suffix = source_path.suffix.lower()
    policy = OCRRoutingPolicy.from_params(params)
    try:
        content = source_path.read_bytes()
    except OSError as exc:
        raise ImageNormalizationError("image_decode_failed", "图片文件读取失败") from exc
    validation = validate_image_bytes(suffix, content, policy)
    temporary_directory = tempfile.TemporaryDirectory(prefix="yuxi-ocr-frame-")
    output_path = Path(temporary_directory.name) / "frame.png"
    started_at = time.monotonic()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                image.seek(0)
                image.load()
                image.convert("RGB").save(output_path, format="PNG", optimize=False)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise ImageNormalizationError("image_decode_failed", "图片首帧规范化失败")
        if output_path.stat().st_size > policy.max_normalized_image_bytes:
            raise ImageNormalizationError("image_too_large", "规范化图片超过安全大小限制")
        duration = time.monotonic() - started_at
        if duration > policy.max_image_decode_seconds:
            raise ImageNormalizationError("image_decode_failed", "图片解码超时")
    except Exception:
        temporary_directory.cleanup()
        raise
    return NormalizedOCRImage(
        path=output_path,
        metadata={
            "original_format": suffix.removeprefix("."),
            "normalized_format": "png",
            "frame_count": validation.frame_count,
            "selected_frames": [0],
            "animation_ignored": validation.animated,
            "normalization_duration_ms": max(0, round(duration * 1000)),
        },
        _temporary_directory=temporary_directory,
    )


def get_image_format_capability(suffix: str) -> dict[str, Any]:
    normalized_suffix = suffix.lower()
    enabled = normalized_suffix != ".webp" or features.check("webp")
    return {
        "extension": normalized_suffix,
        "enabled": bool(enabled),
        "requires_converter": False,
        "availability": "available" if enabled else "decoder_unavailable",
        "reason": None if enabled else "当前 Pillow 未启用 WebP 解码能力",
    }
