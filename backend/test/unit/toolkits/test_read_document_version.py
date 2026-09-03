from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from yuxi.agents.toolkits.kbs import tools
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.document_diff_service import DocumentDiffNotFoundError, DocumentDiffService
from yuxi.services.knowledge_source_version_service import KnowledgeSourceVersionService

# =====================================================================
# read_document_version 只读历史归档版本工具测试
#
# 场景：普通检索只能看到当前版本；被替换归档的旧版本需要按“家族名+版本号”读取。
# 版本号以文件名内嵌标签为准（如《测试文档-v1.1.docx》→ V1.1），内部 document_version
# 只是链序号，不直接当用户版本号。
# =====================================================================

_CURRENT = SimpleNamespace(file_id="file-v1.2", filename="测试文档-v1.2.docx")
_ARCHIVE_V11 = SimpleNamespace(file_id="file-v1.1", filename="测试文档-v1.1.docx")
_VISIBLE_ONE = [{"kb_id": "db-1", "name": "FAQ"}]
_ARCHIVE_TEXT = "第一版完整正文：这是测试文档 1.1 的内容。\n历史章节若干。"


def _tool_callable(tool):
    callback = getattr(tool, "coroutine", None)
    if callback is not None:
        return callback
    callback = getattr(tool, "func", None)
    if callback is not None:
        return callback
    raise AssertionError(f"{tool.name} tool has no callable entry")


async def _run_tool(**kwargs):
    result = _tool_callable(tools.read_document_version)(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _fake_visible_kbs(runtime):
    return _VISIBLE_ONE


async def _fake_visible_two_kbs(runtime):
    return [{"kb_id": "db-1", "name": "FAQ"}, {"kb_id": "db-2", "name": "运营"}]


def _patch_repo_and_chains(monkeypatch, *, current, histories, visible=_VISIBLE_ONE):
    """装配 repo 当前文件扫描 + 版本链解析 + 可见库解析。"""

    async def _fake_visible(runtime):
        return visible

    async def _fake_list_by_kb_id_after(self, kb_id, *, after_file_id=None, limit=500, files_only=False):
        return [current]

    async def _fake_list_for_current_files(self, *, kb_id, file_ids):
        return [
            {
                "file_id": current.file_id,
                "filename": current.filename,
                "document_version": len(histories) + 1,
                "history_versions": [
                    {
                        "file_id": h.file_id,
                        "filename": h.filename,
                        "document_version": index,
                        "updated_at": "2026-01-02T00:00:00Z",
                    }
                    for index, h in enumerate(histories, start=1)
                ],
            }
        ]

    monkeypatch.setattr(tools, "_resolve_visible_knowledge_bases_for_query", _fake_visible)
    monkeypatch.setattr(KnowledgeFileRepository, "list_by_kb_id_after", _fake_list_by_kb_id_after)
    monkeypatch.setattr(
        KnowledgeSourceVersionService,
        "list_for_current_files",
        _fake_list_for_current_files,
    )


def _patch_version_text(monkeypatch, text: str = _ARCHIVE_TEXT):
    async def _fake_get_version_text(self, *, kb_id, file_id):
        return text

    monkeypatch.setattr(DocumentDiffService, "get_version_text", _fake_get_version_text)


@pytest.mark.asyncio
async def test_reads_named_archived_version(monkeypatch) -> None:
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )
    _patch_version_text(monkeypatch)

    runtime = SimpleNamespace(context=SimpleNamespace())
    result = await _run_tool(document_name="测试文档", document_version="1.1", runtime=runtime)

    assert result["ok"] is True
    assert result["file_id"] == "file-v1.1"
    assert result["filename"] == "测试文档-v1.1.docx"
    assert result["version"] == "1.1"
    assert result["current_version"] == {"filename": "测试文档-v1.2.docx", "version": "1.2"}
    assert result["content"] == _ARCHIVE_TEXT
    assert result["truncated"] is False
    assert "不代表当前生效内容" in result["note"]


@pytest.mark.asyncio
async def test_reads_archive_when_version_comes_from_filename(monkeypatch) -> None:
    """document_version 缺省时，从 document_name 尾部版本后缀解析要读的归档。"""
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )
    _patch_version_text(monkeypatch)

    result = await _run_tool(
        document_name="测试文档-v1.1.docx",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is True
    assert result["file_id"] == "file-v1.1"
    assert result["version"] == "1.1"


@pytest.mark.asyncio
async def test_auto_reads_single_history_without_version(monkeypatch) -> None:
    """唯一历史归档且未指定版本（如用户说“上个版本”）→ 直接返回该归档正文。"""
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )
    _patch_version_text(monkeypatch)

    result = await _run_tool(document_name="测试文档", runtime=SimpleNamespace(context=SimpleNamespace()))

    assert result["ok"] is True
    assert result["file_id"] == "file-v1.1"


@pytest.mark.asyncio
async def test_lists_archives_when_multiple_and_no_version(monkeypatch) -> None:
    archive_two = SimpleNamespace(file_id="file-v1.3", filename="测试文档-v1.3.docx")
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11, archive_two],
    )

    result = await _run_tool(document_name="测试文档", runtime=SimpleNamespace(context=SimpleNamespace()))

    assert result["ok"] is True
    assert result["action"] == "list"
    assert "content" not in result
    families = result["families"][0]
    assert families["current_file"]["filename"] == "测试文档-v1.2.docx"
    versions = [h["version"] for h in families["history_versions"]]
    assert versions == ["1.1", "1.3"]


@pytest.mark.asyncio
async def test_refuses_current_version_target(monkeypatch) -> None:
    """请求的版本等于当前版本 → 引导用普通检索，不返回当前正文。"""
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )

    result = await _run_tool(
        document_name="测试文档",
        document_version="1.2",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is False
    assert result["reason"] == "target_is_current"
    assert "query_kb" in result["message"]


@pytest.mark.asyncio
async def test_returns_clean_message_when_version_not_found(monkeypatch) -> None:
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )

    result = await _run_tool(
        document_name="测试文档",
        document_version="9.9",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is False
    assert result["reason"] == "version_not_found"
    assert "V9.9" in result["message"]
    assert result["history_versions"][0][0]["filename"] == "测试文档-v1.1.docx"


@pytest.mark.asyncio
async def test_no_history_message(monkeypatch) -> None:
    _patch_repo_and_chains(monkeypatch, current=_CURRENT, histories=[])

    result = await _run_tool(
        document_name="测试文档",
        document_version="1.1",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is False
    assert result["reason"] == "no_history"
    assert "没有可读的历史归档版本" in result["message"]


@pytest.mark.asyncio
async def test_unknown_kb_name_message(monkeypatch) -> None:
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )

    result = await _run_tool(
        document_name="测试文档",
        kb_name="不存在的库",
        document_version="1.1",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert isinstance(result, str)
    assert "不存在或当前会话未启用" in result


@pytest.mark.asyncio
async def test_no_matching_document_message(monkeypatch) -> None:
    _patch_repo_and_chains(monkeypatch, current=_CURRENT, histories=[_ARCHIVE_V11])
    result = await _run_tool(
        document_name="完全不存在的文档",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )
    assert isinstance(result, str)
    assert "未找到名称包含" in result


@pytest.mark.asyncio
async def test_ambiguous_across_multiple_kbs(monkeypatch) -> None:
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
        visible=[{"kb_id": "db-1", "name": "FAQ"}, {"kb_id": "db-2", "name": "运营"}],
    )

    result = await _run_tool(
        document_name="测试文档",
        document_version="1.1",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is False
    assert result["reason"] == "ambiguous_family"
    assert len(result["matches"]) == 2


@pytest.mark.asyncio
async def test_long_content_is_head_tail_truncated(monkeypatch) -> None:
    monkeypatch.setenv("VERSION_ASK_MAX_INPUT_CHARS", "20")
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )
    long_text = "开头。" + "中间段落内容。" * 200 + "结尾。"
    _patch_version_text(monkeypatch, text=long_text)

    result = await _run_tool(
        document_name="测试文档",
        document_version="1.1",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["truncated"] is True
    assert "开头。" in result["content"]
    assert "结尾。" in result["content"]
    assert len(result["content"]) < len(long_text)


@pytest.mark.asyncio
async def test_archive_source_unavailable_message(monkeypatch) -> None:
    _patch_repo_and_chains(
        monkeypatch,
        current=_CURRENT,
        histories=[_ARCHIVE_V11],
    )

    async def _raise_missing(self, *, kb_id, file_id):
        raise DocumentDiffNotFoundError("missing")

    monkeypatch.setattr(DocumentDiffService, "get_version_text", _raise_missing)

    result = await _run_tool(
        document_name="测试文档",
        document_version="1.1",
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert result["ok"] is False
    assert result["reason"] == "version_source_unavailable"
