from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.document_diff_service import (
    DIFF_CONTEXT_LINES,
    DocumentDiffFamilyMismatchError,
    DocumentDiffNotFoundError,
    DocumentDiffService,
    compute_line_diff,
)


# =============================================================
# compute_line_diff 纯函数
# =============================================================


def _summarize(result):
    return {
        "identical": result["identical"],
        "stats": result["stats"],
        "hunk_count": len(result["hunks"]),
    }


def test_diff_identical_texts_yield_no_hunks():
    text = "line1\nline2\nline3\n"
    result = compute_line_diff(text, text)
    assert result["identical"] is True
    assert result["hunks"] == []
    assert result["stats"] == {"added_lines": 0, "removed_lines": 0, "unchanged_lines": 3}


def test_diff_pure_insert():
    old = "line1\nline3\n"
    new = "line1\nline2\nline3\n"
    result = compute_line_diff(old, new)
    assert _summarize(result) == {
        "identical": False,
        "stats": {"added_lines": 1, "removed_lines": 0, "unchanged_lines": 2},
        "hunk_count": 1,
    }
    types = [item["type"] for item in result["hunks"][0]["lines"]]
    assert types == ["ctx", "add", "ctx"]
    inserted = result["hunks"][0]["lines"][1]
    assert inserted["old_no"] is None
    assert inserted["new_no"] == 2
    assert inserted["text"] == "line2"


def test_diff_pure_delete():
    old = "line1\nline2\nline3\n"
    new = "line1\nline3\n"
    result = compute_line_diff(old, new)
    assert _summarize(result) == {
        "identical": False,
        "stats": {"added_lines": 0, "removed_lines": 1, "unchanged_lines": 2},
        "hunk_count": 1,
    }
    removed = result["hunks"][0]["lines"][1]
    assert removed["type"] == "del"
    assert removed["old_no"] == 2
    assert removed["new_no"] is None


def test_diff_replace():
    old = "line1\nold line\nline3\n"
    new = "line1\nnew line\nline3\n"
    result = compute_line_diff(old, new)
    assert _summarize(result) == {
        "identical": False,
        "stats": {"added_lines": 1, "removed_lines": 1, "unchanged_lines": 2},
        "hunk_count": 1,
    }
    hunk_types = [item["type"] for item in result["hunks"][0]["lines"]]
    assert hunk_types == ["ctx", "del", "add", "ctx"]


def test_diff_far_apart_changes_split_into_hunks():
    old_lines = [f"line{i}" for i in range(30)]
    new_lines = [f"line{i}" for i in range(30)]
    new_lines[1] = "changed-1"
    new_lines[27] = "changed-27"
    old = "\n".join(old_lines)
    new = "\n".join(new_lines)
    result = compute_line_diff(old, new)
    assert len(result["hunks"]) == 2
    assert result["stats"] == {"added_lines": 2, "removed_lines": 2, "unchanged_lines": 28}


def test_diff_nearby_changes_share_context_lines():
    old_lines = [f"line{i}" for i in range(10)]
    new_lines = list(old_lines)
    new_lines[4] = "changed-4"
    new_lines[6] = "changed-6"
    result = compute_line_diff("\n".join(old_lines), "\n".join(new_lines))
    # 两处改动间隔 1 行（line5 是唯一 ctx），上下文必然重叠 → 合并为一个 hunk
    assert len(result["hunks"]) == 1


def test_diff_context_windows_are_bounded():
    old_lines = [f"line{i}" for i in range(100)]
    new_lines = list(old_lines)
    new_lines[50] = "changed-50"
    result = compute_line_diff("\n".join(old_lines), "\n".join(new_lines))
    assert len(result["hunks"]) == 1
    hunk = result["hunks"][0]
    # 改动行 old_no=51（0-based index 50），前后各 DIFF_CONTEXT_LINES 行上下文 → 48..54
    changed_old_no = 50 + 1
    assert hunk["old_start"] == changed_old_no - DIFF_CONTEXT_LINES
    assert hunk["old_end"] == changed_old_no + DIFF_CONTEXT_LINES
    assert hunk["lines"][0]["type"] == "ctx"
    changed_index = next(index for index, item in enumerate(hunk["lines"]) if item["type"] == "del")
    assert changed_index <= DIFF_CONTEXT_LINES
    assert hunk["lines"][-1]["type"] == "ctx"


def test_diff_handles_crlf_and_missing_trailing_newline():
    old = "a\r\nb\r\n"
    new = "a\nb\nc"
    result = compute_line_diff(old, new)
    assert _summarize(result) == {
        "identical": False,
        "stats": {"added_lines": 1, "removed_lines": 0, "unchanged_lines": 2},
        "hunk_count": 1,
    }


# =============================================================
# DocumentDiffService 编排 + 校验
# =============================================================


def _file(
    file_id: str,
    *,
    kb_id: str = "kb-1",
    logical_id: str = "family-1",
    version: int | None = 1,
    current: bool = True,
    markdown_file: str | None = "minio://parsed/kb-1/file.md",
) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        kb_id=kb_id,
        logical_document_id=logical_id,
        document_version=version,
        is_current=current,
        is_folder=False,
        filename=f"{file_id}.md",
        original_filename=f"{file_id}.md",
        markdown_file=markdown_file,
        status="indexed",
        activated_at=datetime(2026, 1, 1),
    )


def _service(repository=None, chunk_repo=None) -> DocumentDiffService:
    service = DocumentDiffService()
    service.file_repo = repository or SimpleNamespace()
    service.chunk_repo = chunk_repo or SimpleNamespace()
    return service


@pytest.mark.asyncio
async def test_diff_versions_reads_text_by_exact_file_id(monkeypatch):
    file_a = _file("file-v1", version=1, current=False)
    file_b = _file("file-v2", version=2, current=True)
    repository = SimpleNamespace(
        get_by_file_id=AsyncMock(side_effect=[file_a, file_b]),
        list_versions=AsyncMock(return_value=[file_a, file_b]),
    )
    service = _service(repository=repository)
    monkeypatch.setattr(
        service,
        "_read_parsed_markdown",
        AsyncMock(side_effect=["# v1\nold line\n", "# v1\nnew line\n"]),
    )

    result = await service.diff_versions(
        kb_id="kb-1",
        version_a_file_id="file-v1",
        version_b_file_id="file-v2",
    )

    assert result["base"]["file_id"] == "file-v1"
    assert result["target"]["file_id"] == "file-v2"
    assert result["identical"] is False
    assert result["stats"] == {"added_lines": 1, "removed_lines": 1, "unchanged_lines": 1}
    service._read_parsed_markdown.assert_awaited_with("minio://parsed/kb-1/file.md")
    assert service._read_parsed_markdown.await_count == 2


@pytest.mark.asyncio
async def test_diff_versions_rejects_cross_family(monkeypatch):
    file_a = _file("file-v1", logical_id="family-1")
    file_b = _file("file-v9", logical_id="family-2")
    repository = SimpleNamespace(
        get_by_file_id=AsyncMock(side_effect=[file_a, file_b]),
    )
    service = _service(repository=repository)

    with pytest.raises(DocumentDiffFamilyMismatchError, match="同一逻辑文档"):
        await service.diff_versions(kb_id="kb-1", version_a_file_id="file-v1", version_b_file_id="file-v9")


@pytest.mark.asyncio
async def test_diff_versions_rejects_missing_file():
    service = _service(repository=SimpleNamespace(get_by_file_id=AsyncMock(return_value=None)))

    with pytest.raises(DocumentDiffNotFoundError, match="不存在"):
        await service.diff_versions(kb_id="kb-1", version_a_file_id="file-v1", version_b_file_id="file-v2")


@pytest.mark.asyncio
async def test_diff_versions_rejects_wrong_kb():
    file_a = _file("file-v1", kb_id="kb-1")
    file_b = _file("file-v2", kb_id="kb-2")
    repository = SimpleNamespace(get_by_file_id=AsyncMock(side_effect=[file_a, file_b]))
    service = _service(repository=repository)

    with pytest.raises(DocumentDiffNotFoundError, match="不存在"):
        await service.diff_versions(kb_id="kb-1", version_a_file_id="file-v2", version_b_file_id="file-v2")


@pytest.mark.asyncio
async def test_diff_versions_identical_text_returns_empty_diff(monkeypatch):
    file_a = _file("file-v1", version=1, current=False)
    file_b = _file("file-v2", version=2, current=True)
    repository = SimpleNamespace(
        get_by_file_id=AsyncMock(side_effect=[file_a, file_b]),
        list_versions=AsyncMock(return_value=[file_a, file_b]),
    )
    service = _service(repository=repository)
    monkeypatch.setattr(service, "_read_parsed_markdown", AsyncMock(side_effect=["same\n", "same\n"]))

    result = await service.diff_versions(kb_id="kb-1", version_a_file_id="file-v1", version_b_file_id="file-v2")

    assert result["identical"] is True
    assert result["hunks"] == []
    assert result["stats"]["unchanged_lines"] == 1


@pytest.mark.asyncio
async def test_get_version_text_falls_back_to_chunks_when_no_markdown():
    file_a = _file("file-v1", markdown_file=None)
    repository = SimpleNamespace(get_by_file_id=AsyncMock(return_value=file_a))
    chunk_repo = SimpleNamespace(
        list_by_file_id=AsyncMock(return_value=[SimpleNamespace(content="chunk-a"), SimpleNamespace(content="chunk-b")])
    )
    service = _service(repository=repository, chunk_repo=chunk_repo)

    text = await service.get_version_text(kb_id="kb-1", file_id="file-v1")

    assert text == "chunk-a\nchunk-b"


@pytest.mark.asyncio
async def test_get_version_text_raises_when_no_text_available():
    file_a = _file("file-v1", markdown_file=None)
    repository = SimpleNamespace(get_by_file_id=AsyncMock(return_value=file_a))
    chunk_repo = SimpleNamespace(list_by_file_id=AsyncMock(return_value=[]))
    service = _service(repository=repository, chunk_repo=chunk_repo)

    with pytest.raises(DocumentDiffNotFoundError, match="没有可对比"):
        await service.get_version_text(kb_id="kb-1", file_id="file-v1")


@pytest.mark.asyncio
async def test_legacy_chain_family_resolution(monkeypatch):
    # 存量：file-a 无 logical_document_id，但经版本链能拉到 file-b → 属同家族
    file_a = _file("file-a", logical_id=None, version=None, current=False)
    file_b = _file("file-b", logical_id="file-a", version=2, current=True)
    repository = SimpleNamespace(
        get_by_file_id=AsyncMock(side_effect=[file_a, file_b]),
        list_versions=AsyncMock(return_value=[file_b, file_a]),
    )
    service = _service(repository=repository)
    monkeypatch.setattr(service, "_read_parsed_markdown", AsyncMock(side_effect=["a\n", "b\n"]))

    result = await service.diff_versions(kb_id="kb-1", version_a_file_id="file-a", version_b_file_id="file-b")

    assert result["base"]["file_id"] == "file-a"
    assert result["identical"] is False
