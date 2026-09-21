from __future__ import annotations

import pytest

import yuxi.agents.toolkits.buildin.tools as buildin_tools

pytestmark = pytest.mark.unit


def test_create_tavily_search_passes_key_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """key 必须显式传给 TavilySearch：不传时 langchain_tavily 只认 os.environ，
    设置页（base.toml / Redis 快照）里的 key 会因为构造时取不到而直接失败。"""
    captured: dict = {}
    monkeypatch.setattr(buildin_tools, "_tavily_search_instance", None)
    monkeypatch.setattr("langchain_tavily.TavilySearch", lambda **kwargs: captured.update(kwargs) or "instance")

    assert buildin_tools._create_tavily_search("tvly-from-settings") == "instance"
    assert captured == {"tavily_api_key": "tvly-from-settings"}


def test_create_tavily_search_reuses_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buildin_tools, "_tavily_search_instance", None)
    monkeypatch.setattr("langchain_tavily.TavilySearch", lambda **kwargs: object())

    first = buildin_tools._create_tavily_search("tvly-a")

    assert buildin_tools._create_tavily_search("tvly-b") is first
