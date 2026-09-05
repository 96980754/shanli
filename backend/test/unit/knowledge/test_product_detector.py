from __future__ import annotations

from types import SimpleNamespace

from yuxi.knowledge.product_detector import ProductDetector, _normalize_detections, top_hit


def test_top_hit_accepts_clear_winner_above_threshold() -> None:
    detections = [
        {"model": "森海克斯D11", "confidence": 0.91},
        {"model": "森海克斯D12", "confidence": 0.20},
    ]
    assert top_hit(detections) == detections[0]


def test_top_hit_rejects_below_threshold() -> None:
    assert top_hit([{"model": "艾尔锐EH01", "confidence": 0.3}]) is None


def test_top_hit_rejects_ambiguous_runner_up_with_margin() -> None:
    detections = [
        {"model": "森海克斯D11", "confidence": 0.60},
        {"model": "森海克斯D12", "confidence": 0.55},
    ]
    assert top_hit(detections, margin=0.2) is None
    assert top_hit(detections, margin=0.0) == detections[0]


def test_top_hit_returns_none_on_empty() -> None:
    assert top_hit([]) is None


def test_available_gating_by_flag_file_and_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRODUCT_DETECT_ENABLED", "auto")
    weights = tmp_path / "best.pt"
    monkeypatch.setenv("PRODUCT_DETECT_MODEL_PATH", str(weights))
    detector = ProductDetector()

    assert detector.available is False  # 权重文件缺失
    weights.write_bytes(b"not-a-pt")

    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name: SimpleNamespace() if name == "ultralytics" else None,
    )
    assert detector.available is True  # 文件 + 库就绪，auto 启用

    monkeypatch.setenv("PRODUCT_DETECT_ENABLED", "0")
    assert detector.available is False  # 显式关闭


async def test_detect_returns_empty_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PRODUCT_DETECT_ENABLED", "0")
    detector = ProductDetector()

    assert await detector.detect(b"whatever") == []


async def test_detect_caches_by_image_content_and_skips_repeat_inference(monkeypatch) -> None:
    monkeypatch.setattr(ProductDetector, "available", True)
    detector = ProductDetector()
    predict_calls: list[bytes] = []

    def fake_predict(payload: bytes) -> list[dict]:
        predict_calls.append(payload)
        return [{"model": "倍控M200", "confidence": 0.9}]

    monkeypatch.setattr(detector, "_predict", fake_predict)

    first = await detector.detect(b"image-a")
    again = await detector.detect(b"image-a")
    other = await detector.detect(b"image-b")

    expected = [{"model": "倍控M200", "confidence": 0.9}]
    assert first == expected
    assert again == expected  # 同图命中缓存
    assert other == expected  # 不同图新推理
    assert predict_calls == [b"image-a", b"image-b"]  # 同图只推理一次


class _FakeArray:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class _FakeTensor:
    def __init__(self, values):
        self._values = values

    def cpu(self):
        return self

    def numpy(self):
        return _FakeArray(self._values)


def test_normalize_merges_by_class_drops_floor_and_sorts_desc() -> None:
    results = [
        SimpleNamespace(
            names={0: "艾尔锐EH01", 1: "森海克斯D11"},
            boxes=SimpleNamespace(
                cls=_FakeTensor([0, 1, 0, 1, 1]),
                conf=_FakeTensor([0.90, 0.80, 0.70, 0.95, 0.03]),  # 0.03 低于底噪阈值
            ),
        )
    ]

    assert _normalize_detections(results) == [
        {"model": "森海克斯D11", "confidence": 0.95},
        {"model": "艾尔锐EH01", "confidence": 0.90},
    ]
