from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError

from yuxi.models.providers.cache import ModelInfo
from yuxi.services import transcription_service


def _upload(data: bytes = b"webm-audio", content_type: str = "audio/webm;codecs=opus") -> UploadFile:
    return UploadFile(filename="recording.webm", file=BytesIO(data), headers={"content-type": content_type})


def _model_info(*, provider_type: str = "openai", model_type: str = "transcription") -> ModelInfo:
    return ModelInfo(
        provider_id="test-provider",
        model_id="whisper-test",
        model_type=model_type,
        display_name="Whisper Test",
        api_key="test-key",
        base_url="https://asr.example.test/v1",
        provider_type=provider_type,
    )


class _FakeClient:
    def __init__(self, create):
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _install_provider(monkeypatch, *, response=None, error: Exception | None = None):
    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        if error:
            raise error
        return response or SimpleNamespace(text="你好，世界", language="zh")

    monkeypatch.setattr(transcription_service, "_resolve_transcription_model", lambda: _model_info())
    monkeypatch.setattr(transcription_service, "_create_transcription_client", lambda _info: _FakeClient(create))
    return captured


@pytest.mark.asyncio
async def test_transcribe_webm_auto_detects_language_and_closes_upload(monkeypatch):
    captured = _install_provider(monkeypatch)
    upload = _upload()

    result = await transcription_service.transcribe_audio(upload)

    assert result == {"text": "你好，世界", "language": "zh"}
    assert captured["model"] == "whisper-test"
    assert captured["file"][1] == b"webm-audio"
    assert captured["file"][2] == "audio/webm"
    assert "language" not in captured
    assert upload.file.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["zh", "en"])
async def test_transcribe_passes_explicit_language(monkeypatch, language):
    captured = _install_provider(monkeypatch, response=SimpleNamespace(text="transcript"))

    result = await transcription_service.transcribe_audio(_upload(), language=language)

    assert result == {"text": "transcript", "language": None}
    assert captured["language"] == language


@pytest.mark.asyncio
async def test_transcribe_rejects_empty_file_and_closes_upload():
    upload = _upload(b"")

    with pytest.raises(HTTPException) as exc_info:
        await transcription_service.transcribe_audio(upload)

    assert exc_info.value.status_code == 400
    assert upload.file.closed is True


@pytest.mark.asyncio
async def test_transcribe_rejects_non_webm_and_closes_upload():
    upload = _upload(content_type="audio/mpeg")

    with pytest.raises(HTTPException) as exc_info:
        await transcription_service.transcribe_audio(upload)

    assert exc_info.value.status_code == 415
    assert upload.file.closed is True


@pytest.mark.asyncio
async def test_transcribe_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(transcription_service, "MAX_TRANSCRIPTION_FILE_SIZE_BYTES", 3)
    upload = _upload(b"1234")

    with pytest.raises(HTTPException) as exc_info:
        await transcription_service.transcribe_audio(upload)

    assert exc_info.value.status_code == 413
    assert upload.file.closed is True


def test_resolve_transcription_model_requires_configuration(monkeypatch):
    monkeypatch.setattr(transcription_service.config, "transcription_model", None)

    with pytest.raises(HTTPException) as exc_info:
        transcription_service._resolve_transcription_model()

    assert exc_info.value.status_code == 503


def test_resolve_transcription_model_rejects_non_compatible_provider(monkeypatch):
    monkeypatch.setattr(transcription_service.config, "transcription_model", "test-provider:whisper-test")
    monkeypatch.setattr(
        transcription_service,
        "resolve_model_spec",
        lambda _spec: _model_info(provider_type="anthropic"),
    )

    with pytest.raises(HTTPException) as exc_info:
        transcription_service._resolve_transcription_model()

    assert exc_info.value.status_code == 503
    assert "不支持" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "message"),
    [
        (
            AuthenticationError(
                "secret upstream response",
                response=httpx.Response(401, request=httpx.Request("POST", "https://asr.example.test")),
                body={"api_key": "must-not-leak"},
            ),
            502,
            "认证失败",
        ),
        (APITimeoutError(httpx.Request("POST", "https://asr.example.test")), 504, "响应超时"),
        (
            APIConnectionError(
                message="internal URL must-not-leak",
                request=httpx.Request("POST", "https://asr.example.test"),
            ),
            502,
            "无法连接",
        ),
        (
            APIStatusError(
                "unsupported endpoint",
                response=httpx.Response(404, request=httpx.Request("POST", "https://asr.example.test")),
                body={"internal": "must-not-leak"},
            ),
            502,
            "不支持",
        ),
    ],
)
async def test_transcribe_normalizes_provider_errors(monkeypatch, error, status_code, message):
    _install_provider(monkeypatch, error=error)
    upload = _upload()

    with pytest.raises(HTTPException) as exc_info:
        await transcription_service.transcribe_audio(upload)

    assert exc_info.value.status_code == status_code
    assert message in exc_info.value.detail
    assert "must-not-leak" not in exc_info.value.detail
    assert upload.file.closed is True
