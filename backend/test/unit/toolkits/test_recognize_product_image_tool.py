from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from yuxi.agents.backends.sandbox.paths import (
    ensure_thread_dirs,
    sandbox_uploads_dir,
    virtual_path_for_thread_file,
)
from yuxi.agents.toolkits.buildin.tools import recognize_product_image
from yuxi.knowledge import product_detector as detector_module

pytestmark = pytest.mark.unit

_ONE_PX_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
_DATA_URI = f"data:image/png;base64,{_ONE_PX_PNG}"


class _FakeDetector:
    def __init__(self, *, available: bool = True, detections: list[dict] | None = None) -> None:
        self._available = available
        self._detections = detections if detections is not None else []
        self.predicted_payloads: list[bytes | str] = []

    @property
    def available(self) -> bool:
        return self._available

    async def detect(self, payload):
        self.predicted_payloads.append(payload)
        return self._detections


def _patch_detector(monkeypatch: pytest.MonkeyPatch, detector: _FakeDetector) -> None:
    monkeypatch.setattr(detector_module, "get_product_detector", lambda: detector)


def _runtime(*, thread_id: str = "thread-1", uid: str = "user-1", messages: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        config={"configurable": {"thread_id": thread_id, "uid": uid}},
        context=SimpleNamespace(thread_id=thread_id, uid=uid),
        state={"messages": messages} if messages is not None else {},
    )


def _user_image_message(data_uri: str = _DATA_URI) -> HumanMessage:
    return HumanMessage(content=[{"type": "image_url", "image_url": {"url": data_uri}}])


@pytest.mark.asyncio
async def test_returns_enabled_false_when_detector_unavailable(monkeypatch) -> None:
    _patch_detector(monkeypatch, _FakeDetector(available=False))

    result = await recognize_product_image.coroutine(runtime=_runtime())

    assert result == {"enabled": False, "hit": False, "detections": []}


@pytest.mark.asyncio
async def test_uses_latest_user_image_when_no_file_path(monkeypatch) -> None:
    fake = _FakeDetector(detections=[{"model": "森海克斯D11", "confidence": 0.91}])
    _patch_detector(monkeypatch, fake)
    runtime = _runtime(messages=[_user_image_message()])

    result = await recognize_product_image.coroutine(runtime=runtime)

    assert result["enabled"] is True
    assert result["source"] == "latest_user_image"
    assert result["hit"] is True
    assert result["detections"] == [{"model": "森海克斯D11", "confidence": 0.91}]
    assert fake.predicted_payloads == [_DATA_URI]


@pytest.mark.asyncio
async def test_reads_file_path_image_and_detects(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("yuxi.config.save_dir", str(tmp_path))
    fake = _FakeDetector(detections=[{"model": "倍控M200", "confidence": 0.95}])
    _patch_detector(monkeypatch, fake)
    thread_id = "thread-1"
    uid = "user-1"
    ensure_thread_dirs(thread_id, uid)
    image_path = sandbox_uploads_dir(thread_id) / "camera.png"
    image_path.write_bytes(b"fake png bytes")
    virtual_path = virtual_path_for_thread_file(thread_id, image_path, uid=uid)

    result = await recognize_product_image.coroutine(file_path=virtual_path, runtime=_runtime())

    assert result["enabled"] is True
    assert result["source"] == "file_path"
    assert result["hit"] is True
    assert fake.predicted_payloads == [b"fake png bytes"]


@pytest.mark.asyncio
async def test_empty_without_image_when_no_file_path(monkeypatch) -> None:
    fake = _FakeDetector()
    _patch_detector(monkeypatch, fake)

    result = await recognize_product_image.coroutine(runtime=_runtime())

    assert result["enabled"] is True
    assert result["hit"] is False
    assert result["detections"] == []
    assert "当前对话中没有可用的用户图片" in result["note"]
    assert fake.predicted_payloads == []  # 没有图片来源就不做推理


@pytest.mark.asyncio
async def test_low_confidence_detection_is_not_hit(monkeypatch) -> None:
    fake = _FakeDetector(detections=[{"model": "艾尔锐EH01", "confidence": 0.2}])
    _patch_detector(monkeypatch, fake)
    runtime = _runtime(messages=[_user_image_message()])

    result = await recognize_product_image.coroutine(runtime=runtime)

    assert result["enabled"] is True
    assert result["hit"] is False
    assert result["detections"] == [{"model": "艾尔锐EH01", "confidence": 0.2}]


@pytest.mark.asyncio
async def test_rejects_out_of_scope_file_path_gracefully(monkeypatch) -> None:
    _patch_detector(monkeypatch, _FakeDetector())

    result = await recognize_product_image.coroutine(file_path="/etc/passwd", runtime=_runtime())

    assert result["enabled"] is True
    assert result["hit"] is False
    assert result["detections"] == []
    assert "只允许解析" in result["note"]
