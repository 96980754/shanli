"""脱敏回填脚本的行为单测。

脚本是一次性的，但它在甲方环境里还要再跑一遍，且**改的是真实库**，所以两个性质必须钉住：
- 一行里的**每一列**都要走到（曾用 `any(... for ...)` 短路，answer 命中后
  evidence_quote 就被跳过，留下「同一条候选里答案已脱敏、引文还是原号码」的残局）；
- 不需要改的行不写回（回填要幂等，不能每次跑都制造 diff）。
"""

from __future__ import annotations

import pytest

from scripts.backfill_udesk_desensitize import redact_rows
from yuxi.storage.postgres.models_udesk import UdeskMessage

pytestmark = pytest.mark.unit


class _Row:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, statement):
        return _FakeResult(self._rows)


async def test_redact_rows_masks_every_field_not_just_the_first_hit():
    """answer 先命中时必须继续处理 evidence_quote。"""
    row = _Row(
        question="如何开通专票？",
        answer="请联系 +8617712340018 办理。",
        evidence_quote="加我 whatsapp +8617712340018",
        ambiguity_note=None,
    )

    updated = await redact_rows(
        _FakeSession([row]), UdeskMessage, ("question", "answer", "evidence_quote", "ambiguity_note")
    )

    assert updated == 1
    assert row.answer == "请联系 +86177****18 办理。"
    assert row.evidence_quote == "加我 whatsapp +86177****18"
    assert row.question == "如何开通专票？"


async def test_redact_rows_skips_clean_rows():
    """已脱敏的行不产生写回，回填可重复执行。"""
    clean = _Row(content="整机质保 2 年。", other="联系 138****78")

    updated = await redact_rows(_FakeSession([clean]), UdeskMessage, ("content", "other"))

    assert updated == 0
    assert clean.content == "整机质保 2 年。"
