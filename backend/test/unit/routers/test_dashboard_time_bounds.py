"""北京日界换算单元测试：YYYY-MM-DD → naive UTC 时间界（左闭右开）。"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import HTTPException

from server.routers.dashboard_router import _beijing_day_bounds

pytestmark = [pytest.mark.unit]


def test_beijing_day_bounds_converts_to_utc_naive():
    # 北京 2026-09-01 00:00 = UTC 2026-08-31 16:00；结束日次日零点为右界（开）
    start_at, end_at = _beijing_day_bounds("2026-09-01", "2026-09-10")
    assert start_at == dt.datetime(2026, 8, 31, 16, 0)
    assert end_at == dt.datetime(2026, 9, 10, 16, 0)


def test_beijing_day_bounds_single_day_range():
    start_at, end_at = _beijing_day_bounds("2026-09-01", "2026-09-01")
    assert start_at == dt.datetime(2026, 8, 31, 16, 0)
    assert end_at == dt.datetime(2026, 9, 1, 16, 0)
    assert start_at < end_at


def test_beijing_day_bounds_allows_partial_bounds():
    assert _beijing_day_bounds("2026-09-01", None) == (dt.datetime(2026, 8, 31, 16, 0), None)
    assert _beijing_day_bounds(None, "2026-09-01") == (None, dt.datetime(2026, 9, 1, 16, 0))
    assert _beijing_day_bounds(None, None) == (None, None)


@pytest.mark.parametrize("bad_date", ["2026/09/01", "20260901", "not-a-date"])
def test_beijing_day_bounds_rejects_bad_format(bad_date):
    with pytest.raises(HTTPException) as exc_info:
        _beijing_day_bounds(bad_date, None)
    assert exc_info.value.status_code == 400


def test_beijing_day_bounds_rejects_reversed_range():
    with pytest.raises(HTTPException) as exc_info:
        _beijing_day_bounds("2026-09-10", "2026-09-01")
    assert exc_info.value.status_code == 400
