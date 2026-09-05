"""本地产品型号目标检测（Ultralytics YOLO）。

把用户上传的产品图片在服务端识别成具体型号（本仓库配套权重为 8 类），作为产品图片识别链路里
「看外观判型号」的本地确定性信号；纯 CPU 推理。

设计要点：
- 懒加载：首次 detect 才 import ultralytics、读权重；加载失败 / 被配置关闭 / 权重缺失 →
  ``available=False``，调用方整体静默降级，不影响既有 VL 看图 / OCR / 以图搜图链路。
- 结果按图片内容 sha 做进程内有界 LRU 缓存，agent 循环内重复图不重复推理。
- 类别名按模型自带 names 取名称（不假定索引顺序，防训练索引漂移）。
- 只返回结构化的 ``[{model, confidence}]``，不做业务判定；阈值/多义判定由调用方经
  ``top_hit`` 统一收敛。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import os
import threading
from collections import OrderedDict
from typing import Any

from yuxi.utils import logger

# 低于该置信度的检测框直接丢弃（缩小缓存体量、避免噪声）。
_DETECT_FLOOR = 0.05
_ENV_TOGGLE_TRUTHY = {"1", "true", "on", "yes"}


def _env_flag_enabled() -> bool:
    """解析 PRODUCT_DETECT_ENABLED：1/true=强制开，0/false=强制关，auto/缺省=交给 available 判。"""
    raw = os.getenv("PRODUCT_DETECT_ENABLED", "auto").strip().lower()
    if raw in _ENV_TOGGLE_TRUTHY:
        return True
    if raw in {"0", "false", "off", "no"}:
        return False
    return True


def _model_path() -> str:
    return os.getenv("PRODUCT_DETECT_MODEL_PATH", "/app/models/best.pt").strip() or "/app/models/best.pt"


def _detect_conf() -> float:
    try:
        return float(os.getenv("PRODUCT_DETECT_CONF", "0.5"))
    except ValueError:
        return 0.5


def _imgsz() -> int:
    try:
        return int(os.getenv("PRODUCT_DETECT_IMGSZ", "640"))
    except ValueError:
        return 640


def _lru_max() -> int:
    try:
        return max(1, int(os.getenv("PRODUCT_DETECT_LRU", "128")))
    except ValueError:
        return 128


def _to_bytes(image: bytes | str) -> bytes | None:
    """归一化输入：原始字节或 ``data:image/...;base64,...`` data URI → 图片字节。"""
    if isinstance(image, bytes):
        return image or None
    if isinstance(image, str) and image.startswith("data:image/"):
        b64 = image.partition(",")[2]
        try:
            payload = base64.b64decode(b64)
        except Exception:
            return None
        return payload or None
    return None


def _decode_image(payload: bytes):
    """图片字节 → RGB numpy 数组（ultralytics 输入）。解码失败返回 None。"""
    try:
        from io import BytesIO

        import numpy as np
        from PIL import Image

        return np.asarray(Image.open(BytesIO(payload)).convert("RGB"))
    except Exception:
        return None


def top_hit(detections: list[dict], *, conf: float | None = None, margin: float = 0.0) -> dict | None:
    """调用方统一的「命中」判定：top-1 置信度 ≥ conf，且（可选）与次高不同类差距 ≥ margin。

    返回命中的检测项或 None（空 / 低于阈值 / 多义）。margin>0 时只有一次清晰压过其它类别才采信，
    用于收敛误检——模棱两可交给既有 VL/OCR/以图搜图兜底，绝不硬答。
    """
    if not detections:
        return None
    threshold = _detect_conf() if conf is None else conf
    best = detections[0]
    if best["confidence"] < threshold:
        return None
    if margin > 0 and len(detections) > 1:
        second = detections[1]
        if second["model"] != best["model"] and best["confidence"] - second["confidence"] < margin:
            return None
    return best


class ProductDetector:
    """进程内单例检测器：懒加载权重 + 内容 sha LRU 缓存。线程安全。"""

    _instance: ProductDetector | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._model: Any = None
        self._load_error: str | None = None
        self._load_lock = threading.Lock()
        self._cache: OrderedDict[str, list[dict]] = OrderedDict()
        self._cache_lock = threading.Lock()

    @classmethod
    def instance(cls) -> ProductDetector:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @property
    def available(self) -> bool:
        """轻量可用性判定（不加载模型）：配置允许 + 权重文件存在 + ultralytics 可导入。"""
        if not _env_flag_enabled():
            return False
        if not os.path.isfile(_model_path()):
            return False
        return importlib.util.find_spec("ultralytics") is not None

    async def detect(self, image: bytes | str) -> list[dict]:
        """识别一张产品图片，返回按置信度降序、同类去重后的 ``[{model, confidence}]``。"""
        if not self.available:
            return []
        payload = _to_bytes(image)
        if not payload:
            return []
        key = hashlib.sha256(payload).hexdigest()
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        detections = await asyncio.to_thread(self._predict, payload)
        self._cache_put(key, detections)
        return detections

    def _predict(self, payload: bytes) -> list[dict]:
        model = self._ensure_model()
        if model is None:
            return []
        image = _decode_image(payload)
        if image is None:
            logger.warning("ProductDetector 无法解码图片（跳过识别）")
            return []
        try:
            results = model.predict(image, imgsz=_imgsz(), conf=_DETECT_FLOOR, verbose=False)
        except Exception as exc:
            logger.error(f"ProductDetector 推理失败: {exc}")
            return []
        return _normalize_detections(results)

    def _ensure_model(self):
        with self._load_lock:
            if self._model is not None:
                return self._model
            if self._load_error is not None:
                return None
            try:
                from ultralytics import YOLO

                path = _model_path()
                self._model = YOLO(path)
                logger.info(f"ProductDetector 已加载 {len(self._model.names)} 类识别模型: {path}")
            except Exception as exc:
                self._load_error = str(exc)
                logger.error(f"ProductDetector 加载失败（整体降级）: {exc}")
                return None
            return self._model

    def _cache_get(self, key: str) -> list[dict] | None:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
            return hit

    def _cache_put(self, key: str, detections: list[dict]) -> None:
        with self._cache_lock:
            self._cache[key] = detections
            self._cache.move_to_end(key)
            while len(self._cache) > _lru_max():
                self._cache.popitem(last=False)


def _normalize_detections(results) -> list[dict]:
    """把 ultralytics 结果归一成按置信度降序、同类去重（取该类最高置信）的列表。"""
    best_by_model: dict[str, float] = {}
    for result in results:
        names = getattr(result, "names", None) or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        try:
            cls_ids = boxes.cls.cpu().numpy().tolist()
            confs = boxes.conf.cpu().numpy().tolist()
        except Exception:
            continue
        for cls_id, confidence in zip(cls_ids, confs):
            if confidence < _DETECT_FLOOR:
                continue
            model_name = str(names.get(int(cls_id), int(cls_id)))
            best_by_model[model_name] = max(best_by_model.get(model_name, 0.0), float(confidence))
    detections = [
        {"model": model_name, "confidence": round(confidence, 4)}
        for model_name, confidence in sorted(best_by_model.items(), key=lambda item: item[1], reverse=True)
    ]
    return detections


def get_product_detector() -> ProductDetector:
    """进程内单例入口（工具与中间件共用，确保 LRU/模型只加载一份）。"""
    return ProductDetector.instance()
