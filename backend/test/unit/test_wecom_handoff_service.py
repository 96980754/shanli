"""企微转人工 WeComCustomerService 单测：按业务线解析 + 通用客服兜底链 + 池内轮替。

语义：业务线绑定客服条目（customer_service_ids）即按线转接；未绑定/unknown/绑定失效
走「通用客服(kefu) 绑定 → 未被任何线绑定的条目默认池」兜底链；全空转人工不可用。
见 docs/vibe/2026-09-03-客服接入设置.md。
"""

import pytest

from yuxi.config.app import config
from yuxi.services import wecom_handoff_service
from yuxi.services.wecom_handoff_service import WeComCustomerService

URL_A = "https://work.weixin.qq.com/kf/a"
URL_B = "https://work.weixin.qq.com/kf/b"
URL_C = "https://work.weixin.qq.com/kf/c"
INVALID_URL = "http://example.com"


def _reset_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    # 直接对模块对象 setattr：dotted-string 形式会在运行期重解析导入路径，
    # 而 test_package_import 等测试会 pop sys.modules["yuxi"]，导致后续解析失败（测试顺序脆弱）。
    monkeypatch.setattr(wecom_handoff_service, "_ROUND_ROBIN_COUNTER", 0)


def _entry(entry_id: str, urls: list[str]) -> dict:
    return {"id": entry_id, "name": f"客服{entry_id}", "urls": list(urls)}


def _line(code: str, bound: list[str] | None = None) -> dict:
    return {"code": code, "name": code, "keywords": [], "customer_service_ids": list(bound or [])}


# ---- 可用性 / https 校验 ----

def test_requires_https_entries():
    assert WeComCustomerService(entries=[]).is_configured is False
    assert WeComCustomerService(entries=[_entry("a", [INVALID_URL])]).is_configured is False


def test_accepts_https_entries(monkeypatch: pytest.MonkeyPatch):
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(entries=[_entry("a", [URL_A])])
    assert service.is_configured is True
    assert service.get_url() == URL_A


# ---- 池内轮替 ----

def test_get_url_round_robins_across_default_pool(monkeypatch: pytest.MonkeyPatch):
    """全都不绑时默认池 = 全部条目：向后兼容旧「全局轮替池」。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(entries=[_entry("a", [URL_A]), _entry("b", [URL_B])])
    assert service.get_url() == URL_A
    assert service.get_url() == URL_B
    assert service.get_url() == URL_A


def test_get_url_round_robins_across_multi_url_entry(monkeypatch: pytest.MonkeyPatch):
    """单个客服条目多账号：URL 并集内轮替扛量。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(entries=[_entry("a", [URL_A, URL_B])])
    assert service.get_url() == URL_A
    assert service.get_url() == URL_B
    assert service.get_url() == URL_A


def test_get_url_skips_invalid_entries(monkeypatch: pytest.MonkeyPatch):
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(entries=[_entry("a", [INVALID_URL]), _entry("b", [URL_A])])
    assert service.get_url() == URL_A
    assert service.get_url() == URL_A


# ---- 按线转接：命中线绑定 ----

def test_get_url_uses_bound_line_entries(monkeypatch: pytest.MonkeyPatch):
    """diaodutai 绑到条目 a：调度台问题只从 a 转，不再动全局池。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B])],
        business_lines=[_line("diaodutai", ["a"]), _line("kefu")],
    )
    assert service.get_url("diaodutai") == URL_A
    assert service.get_url("diaodutai") == URL_A  # 池内只有 a，两连击仍是 a


def test_get_url_round_robins_within_bound_line(monkeypatch: pytest.MonkeyPatch):
    """一条线绑多个客服条目：在绑定集合 URL 并集内轮替。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B])],
        business_lines=[_line("terminal", ["a", "b"]), _line("kefu")],
    )
    assert service.get_url("terminal") == URL_A
    assert service.get_url("terminal") == URL_B
    assert service.get_url("terminal") == URL_A


# ---- 兜底链：kefu 绑定 / 默认池 ----

def test_unknown_falls_back_to_kefu_binding(monkeypatch: pytest.MonkeyPatch):
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B])],
        business_lines=[_line("kefu", ["b"])],
    )
    assert service.get_url("unknown") == URL_B
    assert service.get_url("not-a-line") == URL_B


def test_unbound_line_falls_back_to_default_pool(monkeypatch: pytest.MonkeyPatch):
    """未绑定的线（及无 kefu 线时）：落到未被任何线认领的默认池。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B])],
        business_lines=[_line("diaodutai", ["a"])],
    )
    assert service.get_url("terminal") == URL_B  # 默认池只剩未认领的 b
    assert service.get_url("terminal") == URL_B


def test_kefu_binding_precedes_default_pool(monkeypatch: pytest.MonkeyPatch):
    """有 kefu 绑定时即使默认池非空也先走 kefu 绑定。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B]), _entry("c", [URL_C])],
        business_lines=[_line("kefu", ["b"])],
    )
    assert service.get_url("terminal") == URL_B  # 而非默认池 a/c


def test_ghost_binding_treated_as_unbound(monkeypatch: pytest.MonkeyPatch):
    """手改配置绑定已不存在的客服 id：该线按未绑定走兜底，不抛错不 500。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A]), _entry("b", [URL_B])],
        business_lines=[_line("diaodutai", ["cs-ghost"]), _line("kefu", ["b"])],
    )
    assert service.get_url("diaodutai") == URL_B  # 走 kefu 兜底而非空


def test_empty_when_all_entries_bound_and_no_kefu(monkeypatch: pytest.MonkeyPatch):
    """每条客服都被认领、又无 kefu 线可兜：unbound 的线/unknown 转人工不可用。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(
        entries=[_entry("a", [URL_A])],
        business_lines=[_line("diaodutai", ["a"])],
    )
    assert service.get_url("diaodutai") == URL_A  # 命中线仍可用
    assert service.get_url("terminal") == ""
    assert service.get_url("unknown") == ""


def test_get_url_empty_when_nothing_configured():
    service = WeComCustomerService(entries=[], business_lines=[])
    assert service.get_url() == ""
    assert service.get_url("diaodutai") == ""


# ---- 默认构造读系统配置 ----

def test_default_constructor_reads_app_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        config, "wecom_customer_services", [_entry("a", [URL_A]), _entry("b", [URL_B])]
    )
    monkeypatch.setattr(config, "business_lines", [_line("diaodutai", ["a"]), _line("kefu")])
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService()
    assert service.is_configured is True
    assert service.get_url("diaodutai") == URL_A  # 按线
    assert service.get_url("terminal") == URL_B  # 未绑定 → 默认池


def test_default_constructor_empty_when_config_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "wecom_customer_services", [])
    assert WeComCustomerService().is_configured is False
