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
