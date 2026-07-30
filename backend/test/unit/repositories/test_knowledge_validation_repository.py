from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.knowledge_validation_repository import KnowledgeValidationRepository


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ValidationSession:
    def __init__(self, report=None):
        self.report = report
        self.added = []
        self.executed = []
        self.flush = AsyncMock()

    async def execute(self, statement):
        self.executed.append(statement)
        if len(self.executed) == 1:
            return ScalarResult(self.report)
        return ScalarResult()

    def add(self, record):
        self.added.append(record)


@pytest.mark.asyncio
async def test_replace_for_candidate_persists_ordered_evidence_snapshot():
    session = ValidationSession()

    report, items = await KnowledgeValidationRepository().replace_for_candidate(
        report_id="report-1",
        kb_id="kb-1",
        logical_document_id="logical-1",
        old_file_id="old-1",
        candidate_file_id="candidate-1",
        status="review_required",
        summary={"new_count": 0, "changed_count": 0, "removed_count": 1, "conflict_count": 0},
        items=[
            {
                "change_type": "removed",
                "severity": "high",
                "decision": "pending",
                "fact_key": "product|SUPPORTS|offline",
                "relation": "SUPPORTS",
                "old_fact": {"evidence": {"quote": "支持离线模式"}},
                "new_fact": {"evidence": {"quote": "不再支持离线模式"}},
                "old_evidence": [{"quote": "支持离线模式"}],
                "new_evidence": [{"quote": "不再支持离线模式"}],
                "review_required": True,
                "reason": "显式撤回",
            }
        ],
        session=session,
    )

    assert report.status == "review_required"
    assert report.summary == {"new_count": 0, "changed_count": 0, "removed_count": 1, "conflict_count": 0}
    assert report.removed_count == 1
    assert items[0].item_index == 0
    assert items[0].change_type == "removed"
    assert items[0].new_fact["evidence"]["quote"] == "不再支持离线模式"
    assert session.added == [report, items[0]]
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_decision_only_allows_pending_review():
    report = SimpleNamespace(
        status="review_required",
        decision="pending",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=None,
    )
    session = ValidationSession(report)

    result = await KnowledgeValidationRepository().set_decision(
        report_id="report-1",
        decision="rejected",
        operator_id="admin",
        session=session,
    )

    assert result.status == "rejected"
    assert result.reviewed_by == "admin"
    assert result.reviewed_at is not None


@pytest.mark.asyncio
async def test_replace_for_candidate_rejects_final_report_overwrite():
    session = ValidationSession(SimpleNamespace(status="accepted"))

    with pytest.raises(ValueError, match="不能覆盖"):
        await KnowledgeValidationRepository().replace_for_candidate(
            report_id="report-1",
            kb_id="kb-1",
            logical_document_id="logical-1",
            old_file_id="old-1",
            candidate_file_id="candidate-1",
            status="auto_accepted",
            summary={},
            items=[],
            session=session,
        )
