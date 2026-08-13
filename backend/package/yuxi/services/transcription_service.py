from typing import Any

from fastapi import HTTPException, UploadFile, status
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, AuthenticationError

from yuxi import config
from yuxi.models.providers.cache import ModelInfo, resolve_model_spec
from yuxi.utils.logging_config import logger
from yuxi.utils.upload_utils import read_upload_with_limit

MAX_TRANSCRIPTION_FILE_SIZE_BYTES = 25 * 1024 * 1024
SUPPORTED_TRANSCRIPTION_MIME_TYPES = frozenset({"audio/webm"})
OPENAI_COMPATIBLE_PROVIDER_TYPES = frozenset({"openai", "openrouter"})


def _resolve_transcription_model() -> ModelInfo:
    model_spec = str(config.transcription_model or "").strip()
    if not model_spec:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音转写模型未配置，请联系管理员",
        )

    try:
        model_info = resolve_model_spec(model_spec)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音转写模型不可用，请联系管理员检查配置",
        ) from exc

    if model_info.model_type != "transcription":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音转写模型类型配置错误，请联系管理员",
        )
    if model_info.provider_type not in OPENAI_COMPATIBLE_PROVIDER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="当前语音转写 Provider 不支持 OpenAI-compatible transcription",
        )
    return model_info


def _create_transcription_client(model_info: ModelInfo) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=model_info.api_key or "not-required",
        base_url=model_info.base_url,
        default_headers=model_info.headers,
        timeout=60.0,
    )


def _response_value(response: Any, key: str) -> Any:
    if isinstance(response, dict):
        return response.get(key)
    return getattr(response, key, None)


async def transcribe_audio(upload: UploadFile, *, language: str | None = None) -> dict[str, str | None]:
    content_type = str(upload.content_type or "").split(";", 1)[0].strip().lower()
    try:
        if content_type not in SUPPORTED_TRANSCRIPTION_MIME_TYPES:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="仅支持 WebM 录音")

        try:
            audio_bytes = await read_upload_with_limit(
                upload,
                max_size_bytes=MAX_TRANSCRIPTION_FILE_SIZE_BYTES,
                too_large_message="录音文件不能超过 25 MiB",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)) from exc

        if not audio_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="录音内容为空")

        model_info = _resolve_transcription_model()
        request: dict[str, Any] = {
            "model": model_info.model_id,
            "file": (upload.filename or "recording.webm", audio_bytes, content_type),
        }
        normalized_language = str(language or "").strip()
        if normalized_language:
            request["language"] = normalized_language

        try:
            async with _create_transcription_client(model_info) as client:
                response = await client.audio.transcriptions.create(**request)
        except AuthenticationError as exc:
            logger.warning("ASR Provider authentication failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="语音转写服务认证失败，请联系管理员检查配置",
            ) from exc
        except APITimeoutError as exc:
            logger.warning("ASR Provider request timed out")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="语音转写服务响应超时") from exc
        except APIConnectionError as exc:
            logger.warning("ASR Provider connection failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="无法连接语音转写服务") from exc
        except APIStatusError as exc:
            logger.warning(f"ASR Provider returned HTTP {exc.status_code}")
            message = (
                "当前 Provider 不支持已配置的语音转写接口或模型"
                if exc.status_code in {400, 404, 405}
                else "语音转写服务暂时不可用"
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc
        except Exception as exc:
            logger.warning(f"ASR Provider request failed: {type(exc).__name__}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="语音转写失败，请稍后重试") from exc

        text = str(_response_value(response, "text") or "").strip()
        if not text:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="语音转写未返回有效文本")

        detected_language = str(_response_value(response, "language") or "").strip() or None
        return {"text": text, "language": detected_language}
    finally:
        await upload.close()
