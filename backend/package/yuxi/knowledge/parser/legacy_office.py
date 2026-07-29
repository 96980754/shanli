"""Safe legacy Office conversion into the existing OOXML parser inputs."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OLE_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass(frozen=True, slots=True)
class LegacyOfficeFormat:
    normalized_suffix: str
    libreoffice_filter: str
    required_member: str
    label: str


LEGACY_OFFICE_FORMATS = {
    ".doc": LegacyOfficeFormat(".docx", "docx:Office Open XML Text", "word/document.xml", "DOC"),
    ".xls": LegacyOfficeFormat(".xlsx", "xlsx:Calc MS Excel 2007 XML", "xl/workbook.xml", "XLS"),
    ".ppt": LegacyOfficeFormat(".pptx", "pptx:Impress MS PowerPoint 2007 XML", "ppt/presentation.xml", "PPT"),
}

OOXML_REQUIRED_MEMBERS = {
    ".docx": ("word/document.xml", "DOCX"),
    ".xlsx": ("xl/workbook.xml", "XLSX"),
    ".pptx": ("ppt/presentation.xml", "PPTX"),
}


def _bool_setting(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_setting(value: Any, default: int, minimum: int = 1) -> int:
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class LegacyOfficeSettings:
    enabled: bool
    binary: str | None
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int

    @classmethod
    def from_params(cls, params: dict[str, Any] | None = None) -> LegacyOfficeSettings:
        from yuxi import config

        values = params or {}
        enabled_value = values.get(
            "legacy_office_enabled",
            os.getenv("LEGACY_OFFICE_ENABLED", getattr(config, "legacy_office_enabled", True)),
        )
        binary_value = values.get(
            "libreoffice_binary",
            os.getenv("LIBREOFFICE_BINARY", getattr(config, "libreoffice_binary", "soffice")),
        )
        timeout_value = values.get(
            "legacy_office_timeout_seconds",
            os.getenv(
                "LEGACY_OFFICE_TIMEOUT_SECONDS",
                getattr(config, "legacy_office_timeout_seconds", 90),
            ),
        )
        input_limit = values.get(
            "legacy_office_max_input_bytes",
            os.getenv(
                "LEGACY_OFFICE_MAX_INPUT_BYTES",
                getattr(config, "legacy_office_max_input_bytes", 100 * 1024 * 1024),
            ),
        )
        output_limit = values.get(
            "legacy_office_max_output_bytes",
            os.getenv(
                "LEGACY_OFFICE_MAX_OUTPUT_BYTES",
                getattr(config, "legacy_office_max_output_bytes", 150 * 1024 * 1024),
            ),
        )
        binary = str(binary_value or "").strip() or None
        return cls(
            enabled=_bool_setting(enabled_value, True),
            binary=binary,
            timeout_seconds=_int_setting(timeout_value, 90),
            max_input_bytes=_int_setting(input_limit, 100 * 1024 * 1024),
            max_output_bytes=_int_setting(output_limit, 150 * 1024 * 1024),
        )


class LegacyOfficeConversionError(ValueError):
    """A safe, classified legacy Office failure suitable for persistence."""

    def __init__(self, code: str, message: str, *, parse_metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.parse_metadata = parse_metadata or {}


@dataclass(frozen=True, slots=True)
class LegacyOfficeConversionResult:
    content: bytes
    normalized_suffix: str
    metadata: dict[str, Any]


def validate_ooxml_bytes(suffix: str, content: bytes) -> None:
    """Validate that an OOXML ZIP contains the core member for its extension."""
    normalized_suffix = suffix.lower()
    try:
        required_member, label = OOXML_REQUIRED_MEMBERS[normalized_suffix]
    except KeyError as exc:
        raise ValueError(f"不支持的 Office Open XML 格式: {normalized_suffix}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{label} 文件不是有效的 Office Open XML 容器") from exc
    if "[Content_Types].xml" not in names or required_member not in names:
        raise ValueError(f"{label} 文件容器类型与扩展名不匹配")


class LegacyOfficeConverter:
    """Convert DOC/XLS/PPT to OOXML with an isolated LibreOffice profile."""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.settings = LegacyOfficeSettings.from_params(params)

    @staticmethod
    def resolve_binary(configured: str | None = None) -> str | None:
        candidates = [configured] if configured else ["soffice", "libreoffice"]
        for candidate in candidates:
            if candidate and (resolved := shutil.which(candidate)):
                return resolved
        return None

    @classmethod
    def capability(cls, suffix: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = LegacyOfficeSettings.from_params(params)
        normalized_suffix = suffix.lower()
        spec = LEGACY_OFFICE_FORMATS.get(normalized_suffix)
        if spec is None:
            return {
                "extension": normalized_suffix,
                "enabled": False,
                "requires_converter": True,
                "availability": "unsupported",
                "reason": "该旧 Office 格式不受支持",
            }
        if not settings.enabled:
            return {
                "extension": normalized_suffix,
                "enabled": False,
                "requires_converter": True,
                "availability": "disabled",
                "reason": "旧 Office 转换能力未启用",
            }
        if not cls.resolve_binary(settings.binary):
            return {
                "extension": normalized_suffix,
                "enabled": False,
                "requires_converter": True,
                "availability": "converter_unavailable",
                "reason": "LibreOffice 旧 Office 转换服务未安装或不可用",
            }
        return {
            "extension": normalized_suffix,
            "enabled": True,
            "requires_converter": True,
            "availability": "available",
            "reason": None,
        }

    def validate_input(self, suffix: str, content: bytes) -> None:
        normalized_suffix = suffix.lower()
        spec = LEGACY_OFFICE_FORMATS.get(normalized_suffix)
        if spec is None:
            raise LegacyOfficeConversionError("unsupported_format", "不支持的旧 Office 文件格式")
        if len(content) > self.settings.max_input_bytes:
            raise LegacyOfficeConversionError(
                "file_too_large",
                f"{spec.label} 文件超过转换大小限制",
            )
        if not content.startswith(OLE_COMPOUND_FILE_SIGNATURE):
            raise LegacyOfficeConversionError(
                "invalid_file_signature",
                f"{spec.label} 文件不是有效的 OLE Compound File",
            )
        capability = self.capability(normalized_suffix, params=self._settings_as_params())
        if not capability["enabled"]:
            code = (
                "converter_unavailable"
                if capability["availability"] == "converter_unavailable"
                else "unsupported_format"
            )
            raise LegacyOfficeConversionError(code, str(capability["reason"]))

    def convert(self, source_path: Path) -> LegacyOfficeConversionResult:
        suffix = source_path.suffix.lower()
        spec = LEGACY_OFFICE_FORMATS.get(suffix)
        if spec is None:
            raise LegacyOfficeConversionError("unsupported_format", "不支持的旧 Office 文件格式")
        try:
            content = source_path.read_bytes()
        except OSError as exc:
            raise LegacyOfficeConversionError("conversion_failed", f"{spec.label} 文件读取失败") from exc
        self.validate_input(suffix, content)
        binary = self.resolve_binary(self.settings.binary)
        if not binary:
            raise LegacyOfficeConversionError("converter_unavailable", "LibreOffice 旧 Office 转换服务未安装或不可用")

        started_at = time.monotonic()
        base_metadata = {
            "original_format": suffix.removeprefix("."),
            "normalized_format": spec.normalized_suffix.removeprefix("."),
            "conversion_required": True,
            "converter_name": "libreoffice",
            "converter_version": self._read_version(binary),
            "conversion_warnings": [],
        }
        try:
            with tempfile.TemporaryDirectory(prefix="yuxi-legacy-office-") as temp_dir:
                temp_path = Path(temp_dir)
                input_path = temp_path / f"source{suffix}"
                output_path = temp_path / f"source{spec.normalized_suffix}"
                profile_path = temp_path / "profile"
                profile_path.mkdir()
                input_path.write_bytes(content)
                command = [
                    binary,
                    "--headless",
                    "--nologo",
                    "--nofirststartwizard",
                    "--nodefault",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile_path.resolve().as_uri()}",
                    "--convert-to",
                    spec.libreoffice_filter,
                    "--outdir",
                    str(temp_path),
                    str(input_path),
                ]
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        timeout=self.settings.timeout_seconds,
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise LegacyOfficeConversionError(
                        "conversion_timeout",
                        f"{spec.label} 文件转换超时",
                        parse_metadata=self._failure_metadata(base_metadata, started_at, "conversion_timeout"),
                    ) from exc
                if completed.returncode != 0:
                    diagnostic = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="ignore").lower()
                    code = (
                        "encrypted_document"
                        if any(word in diagnostic for word in ("password", "encrypted"))
                        else "conversion_failed"
                    )
                    message = (
                        "文件已加密或受密码保护，无法转换"
                        if code == "encrypted_document"
                        else f"{spec.label} 文件转换失败"
                    )
                    raise LegacyOfficeConversionError(
                        code,
                        message,
                        parse_metadata=self._failure_metadata(base_metadata, started_at, code),
                    )
                if not output_path.is_file():
                    raise LegacyOfficeConversionError(
                        "conversion_failed",
                        f"{spec.label} 文件转换失败，未生成目标文件",
                        parse_metadata=self._failure_metadata(base_metadata, started_at, "missing_output"),
                    )
                output = output_path.read_bytes()
                if not output or len(output) > self.settings.max_output_bytes:
                    raise LegacyOfficeConversionError(
                        "invalid_converted_output",
                        f"{spec.label} 文件转换结果无效",
                        parse_metadata=self._failure_metadata(base_metadata, started_at, "invalid_output_size"),
                    )
                try:
                    validate_ooxml_bytes(spec.normalized_suffix, output)
                except ValueError as exc:
                    raise LegacyOfficeConversionError(
                        "invalid_converted_output",
                        f"{spec.label} 文件转换结果格式不匹配",
                        parse_metadata=self._failure_metadata(base_metadata, started_at, "invalid_ooxml"),
                    ) from exc
        except LegacyOfficeConversionError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise LegacyOfficeConversionError(
                "conversion_failed",
                f"{spec.label} 文件转换失败",
                parse_metadata=self._failure_metadata(base_metadata, started_at, "conversion_failed"),
            ) from exc

        metadata = dict(base_metadata)
        metadata["conversion_duration_ms"] = max(0, round((time.monotonic() - started_at) * 1000))
        return LegacyOfficeConversionResult(
            content=output,
            normalized_suffix=spec.normalized_suffix,
            metadata=metadata,
        )

    def _settings_as_params(self) -> dict[str, Any]:
        return {
            "legacy_office_enabled": self.settings.enabled,
            "libreoffice_binary": self.settings.binary,
            "legacy_office_timeout_seconds": self.settings.timeout_seconds,
            "legacy_office_max_input_bytes": self.settings.max_input_bytes,
            "legacy_office_max_output_bytes": self.settings.max_output_bytes,
        }

    def _read_version(self, binary: str) -> str:
        try:
            completed = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                timeout=min(self.settings.timeout_seconds, 10),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        if completed.returncode != 0:
            return "unknown"
        value = (completed.stdout or b"").decode("utf-8", errors="ignore").strip()
        return value[:120] or "unknown"

    @staticmethod
    def _failure_metadata(
        base_metadata: dict[str, Any],
        started_at: float,
        reason: str,
    ) -> dict[str, Any]:
        metadata = dict(base_metadata)
        metadata["conversion_duration_ms"] = max(0, round((time.monotonic() - started_at) * 1000))
        metadata["conversion_warnings"] = [reason]
        return metadata


def get_legacy_office_capability(suffix: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return LegacyOfficeConverter.capability(suffix, params=params)


def validate_legacy_office_bytes(suffix: str, content: bytes, params: dict[str, Any] | None = None) -> None:
    LegacyOfficeConverter(params).validate_input(suffix, content)
