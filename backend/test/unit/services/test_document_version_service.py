from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from yuxi.services.document_version_service import DocumentVersionService


@pytest.mark.asyncio
async def test_activation_rejects_candidate_without_validation_report():
    service = DocumentVersionService()
    service._get_candidate = AsyncMock(return_value=SimpleNamespace())
    service.validation_repo = SimpleNamespace(get_by_candidate=AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="尚未完成知识变更分析"):
        await service.activate_candidate(
            kb_id="kb",
            candidate_file_id="candidate",
            expected_current_file_id="current",
            operator_id="admin",
            accept_conflicts=False,
        )


@pytest.mark.asyncio
async def test_activation_rejects_failed_validation_report():
    service = DocumentVersionService()
    service._get_candidate = AsyncMock(return_value=SimpleNamespace())
    service.validation_repo = SimpleNamespace(get_by_candidate=AsyncMock(return_value=SimpleNamespace(status="failed")))

    with pytest.raises(ValueError, match="尚未完成知识变更分析"):
        await service.activate_candidate(
            kb_id="kb",
            candidate_file_id="candidate",
            expected_current_file_id="current",
            operator_id="admin",
            accept_conflicts=True,
        )


@pytest.mark.asyncio
async def test_review_required_report_requires_explicit_acceptance():
    service = DocumentVersionService()
    service._get_candidate = AsyncMock(return_value=SimpleNamespace())
    service.validation_repo = SimpleNamespace(
        get_by_candidate=AsyncMock(return_value=SimpleNamespace(status="review_required"))
    )

    with pytest.raises(ValueError, match="CONFLICT_REVIEW_REQUIRED"):
        await service.activate_candidate(
            kb_id="kb",
            candidate_file_id="candidate",
            expected_current_file_id="current",
            operator_id="admin",
            accept_conflicts=False,
        )


@pytest.mark.asyncio
async def test_activation_publishes_candidate_before_archiving_old(monkeypatch):
    events = []
    report = SimpleNamespace(status="auto_accepted", report_id="report", published_at=None)
    old = SimpleNamespace(file_id="old")
    current = SimpleNamespace(file_id="candidate", activated_at="activated")
    service = DocumentVersionService()
    service._get_candidate = AsyncMock(return_value=SimpleNamespace())
    service.validation_repo = SimpleNamespace(
        get_by_candidate=AsyncMock(return_value=report),
        set_decision=AsyncMock(),
        mark_published=AsyncMock(side_effect=lambda **_kwargs: setattr(report, "status", "published")),
    )
    service.conflict_repo = SimpleNamespace(accept_candidate=AsyncMock())
    service.kb_repo = SimpleNamespace(
        get_by_kb_id=AsyncMock(return_value=SimpleNamespace(additional_params={"graph_build_config": {"locked": True}}))
    )
    service.file_repo = SimpleNamespace(
        activate_candidate=AsyncMock(side_effect=lambda **_kwargs: events.append("activate") or (old, current))
    )
    service._cleanup_old_version = AsyncMock(side_effect=lambda *_args: events.append("archive") or [])

    graph_service = SimpleNamespace(
        publish_file_graph=AsyncMock(side_effect=lambda *_args: events.append("publish")),
        delete_file_graph=AsyncMock(),
        chunk_repo=SimpleNamespace(reset_graph_state_by_file_id=AsyncMock()),
    )
    monkeypatch.setattr("yuxi.services.document_version_service.MilvusGraphService", lambda: graph_service)

    @asynccontextmanager
    async def session_context():
        yield SimpleNamespace()

    monkeypatch.setattr("yuxi.services.document_version_service.pg_manager.get_async_session_context", session_context)

    result = await service.activate_candidate(
        kb_id="kb",
        candidate_file_id="candidate",
        expected_current_file_id="old",
        operator_id="admin",
        accept_conflicts=False,
    )

    assert events == ["publish", "activate", "archive"]
    assert result["activated"] is True
    assert report.status == "published"
    service.validation_repo.mark_published.assert_awaited_once_with(
        report_id="report", published_at="activated", session=ANY
    )
    service.validation_repo.set_decision.assert_not_awaited()
    graph_service.delete_file_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_activation_failure_removes_candidate_graph_and_keeps_old(monkeypatch):
    report = SimpleNamespace(status="auto_accepted", report_id="report", published_at=None)
    service = DocumentVersionService()
    service._get_candidate = AsyncMock(return_value=SimpleNamespace())
    service.validation_repo = SimpleNamespace(
        get_by_candidate=AsyncMock(return_value=report),
        set_decision=AsyncMock(),
        mark_published=AsyncMock(side_effect=lambda **_kwargs: setattr(report, "status", "published")),
    )
    service.conflict_repo = SimpleNamespace(accept_candidate=AsyncMock())
    service.kb_repo = SimpleNamespace(
        get_by_kb_id=AsyncMock(return_value=SimpleNamespace(additional_params={"graph_build_config": {"locked": True}}))
    )
    service.file_repo = SimpleNamespace(activate_candidate=AsyncMock(side_effect=ValueError("VERSION_CHANGED")))
    service._cleanup_old_version = AsyncMock()

    graph_service = SimpleNamespace(
        publish_file_graph=AsyncMock(),
        delete_file_graph=AsyncMock(),
        chunk_repo=SimpleNamespace(reset_graph_state_by_file_id=AsyncMock()),
    )
    monkeypatch.setattr("yuxi.services.document_version_service.MilvusGraphService", lambda: graph_service)

    @asynccontextmanager
    async def session_context():
        yield SimpleNamespace()

    monkeypatch.setattr("yuxi.services.document_version_service.pg_manager.get_async_session_context", session_context)

    with pytest.raises(ValueError, match="VERSION_CHANGED"):
        await service.activate_candidate(
            kb_id="kb",
            candidate_file_id="candidate",
            expected_current_file_id="old",
            operator_id="admin",
            accept_conflicts=False,
        )

    graph_service.publish_file_graph.assert_awaited_once_with("kb", "candidate")
    graph_service.delete_file_graph.assert_awaited_once_with("kb", "candidate")
    graph_service.chunk_repo.reset_graph_state_by_file_id.assert_awaited_once_with("candidate")
    service._cleanup_old_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_changes_skips_and_auto_accepts_when_graph_not_configured(monkeypatch):
    """未配置图谱时跳过变更分析：写 auto_accepted 报告、空冲突，且不实例化图谱服务。

    对齐 changelog 设计（“未配置图谱时则明确提示已跳过冲突检测并自动启用新版”），
    不应像旧实现那样硬失败把候选卡在 validation_failed。
    """
    service = DocumentVersionService()
    candidate = SimpleNamespace(
        file_id="candidate",
        logical_document_id="logical",
        filename="candidate.md",
        document_version=2,
    )
    service._get_candidate = AsyncMock(return_value=candidate)
    service.file_repo = SimpleNamespace(
        get_by_file_id=AsyncMock(return_value=SimpleNamespace(filename="old.md", document_version=1))
    )
    service.kb_repo = SimpleNamespace(get_by_kb_id=AsyncMock(return_value=SimpleNamespace(additional_params={})))
    report = SimpleNamespace(report_id="report")
    service.validation_repo = SimpleNamespace(replace_for_candidate=AsyncMock(return_value=(report, [])))
    service.conflict_repo = SimpleNamespace(replace_for_candidate=AsyncMock())

    @asynccontextmanager
    async def session_context():
        yield SimpleNamespace()

    monkeypatch.setattr("yuxi.services.document_version_service.pg_manager.get_async_session_context", session_context)

    def _unexpected_graph(*_args, **_kwargs):
        raise AssertionError("未配置图谱时不应实例化 MilvusGraphService")

    monkeypatch.setattr("yuxi.services.document_version_service.MilvusGraphService", _unexpected_graph)

    result = await service.analyze_changes(kb_id="kb", old_file_id="old", candidate_file_id="candidate")

    assert result["status"] == "auto_accepted"
    assert result["items"] == []
    assert result["report_id"] == "report"
    assert result["summary"]["skip_reason"] == "graph_not_configured"
    assert "跳过" in result["summary"]["message"]
    service.validation_repo.replace_for_candidate.assert_awaited_once_with(
        report_id="validation_candidate",
        kb_id="kb",
        logical_document_id="logical",
        old_file_id="old",
        candidate_file_id="candidate",
        status="auto_accepted",
        summary=result["summary"],
        items=[],
        session=ANY,
        report_metadata={
            "old_filename": "old.md",
            "old_document_version": 1,
            "candidate_filename": "candidate.md",
            "candidate_document_version": 2,
            "extraction_schema_version": 2,
        },
    )
    service.conflict_repo.replace_for_candidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_candidate_auto_activates_new_version_without_graph(monkeypatch):
    """无图谱时 process_candidate 全链路：变更分析跳过→自动激活新版，且不发布图谱。

    analyze_changes 的跳过分支已在单独测试覆盖，此处固定其返回 auto_accepted，
    让真实的 activate_candidate 跑通——验证用户主流程“没有图谱也能完成版本更新”。
    """
    service = DocumentVersionService()
    candidate = SimpleNamespace(file_id="candidate", supersedes_file_id="old", logical_document_id="logical")
    service._get_candidate = AsyncMock(return_value=candidate)
    monkeypatch.setattr("yuxi.services.document_version_service.knowledge_base.parse_file", AsyncMock())
    monkeypatch.setattr("yuxi.services.document_version_service.knowledge_base.index_file", AsyncMock())
    service.analyze_changes = AsyncMock(
        return_value={"status": "auto_accepted", "items": [], "summary": {"message": "跳过"}}
    )

    old = SimpleNamespace(file_id="old")
    current = SimpleNamespace(file_id="candidate", activated_at="ts")
    service.file_repo = SimpleNamespace(
        update_fields=AsyncMock(),
        activate_candidate=AsyncMock(return_value=(old, current)),
    )
    service.validation_repo = SimpleNamespace(
        get_by_candidate=AsyncMock(
            return_value=SimpleNamespace(status="auto_accepted", report_id="report", published_at=None)
        ),
        mark_published=AsyncMock(),
    )
    service.conflict_repo = SimpleNamespace()
    service.kb_repo = SimpleNamespace(get_by_kb_id=AsyncMock(return_value=SimpleNamespace(additional_params={})))
    service._cleanup_old_version = AsyncMock(return_value=[])

    @asynccontextmanager
    async def session_context():
        yield SimpleNamespace()

    monkeypatch.setattr("yuxi.services.document_version_service.pg_manager.get_async_session_context", session_context)

    result = await service.process_candidate(kb_id="kb", candidate_file_id="candidate", operator_id="admin")

    assert result["activated"] is True
    statuses = [call.kwargs["data"]["status"] for call in service.file_repo.update_fields.await_args_list]
    assert "validation_accepted" in statuses, "分析通过后候选应先置为 validation_accepted 再激活"
    service.file_repo.activate_candidate.assert_awaited_once()
    service.validation_repo.mark_published.assert_awaited_once_with(
        report_id="report", published_at="ts", session=ANY
    )
