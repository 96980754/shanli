"""retrieval_injection_char_limit 必须有下界（回归）。

该值配成 0/负数时 _apply_injection_budget 首轮即返回空 results，表现为系统性
「知识不足」假拒答，且从指标上无法看出是配置错误。Redis 运行时同步是裸 setattr
不过校验，文件/env 路径的 ge 约束是配置加载侧最后一道防线。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yuxi.config.app import Config


def test_retrieval_injection_char_limit_rejects_below_floor(tmp_path):
    with pytest.raises(ValidationError):
        Config(save_dir=str(tmp_path), retrieval_injection_char_limit=100)


def test_retrieval_injection_char_limit_accepts_floor(tmp_path):
    cfg = Config(save_dir=str(tmp_path), retrieval_injection_char_limit=2000)
    assert cfg.retrieval_injection_char_limit == 2000
