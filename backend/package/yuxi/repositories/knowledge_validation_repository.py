from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeValidationItem, KnowledgeValidationReport
from yuxi.utils.datetime_utils import utc_now_naive


class KnowledgeValidationRepository:
    async def replace_for_candidate(
        self,
        *,
        report_id: str,
        kb_id: str,
        logical_document_id: str,
        old_file_id: str,
        candidate_file_id: str,
        status: str,
        summary: dict[str, Any],
        items: list[dict[str, Any]],
        session: AsyncSession,
        report_metadata: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeValidationReport, list[KnowledgeValidationItem]]:
        if status not in {"auto_accepted", "review_required"}:
            raise ValueError("完成的验证报告状态必须是 auto_accepted 或 review_required")

        result = await session.execute(
            select(KnowledgeValidationReport)
            .where(KnowledgeValidationReport.candidate_file_id == candidate_file_id)
            .with_for_update()
        )
        report = result.scalar_one_or_none()
        if report is not None and report.status in {"auto_accepted", "accepted", "rejected"}:
            raise ValueError("验证报告已完成决策，不能覆盖")

        if report is None:
            report = KnowledgeValidationReport(
                report_id=report_id,
                kb_id=kb_id,
                logical_document_id=logical_document_id,
                old_file_id=old_file_id,
                candidate_file_id=candidate_file_id,
            )
            session.add(report)
        else:
            await session.execute(
                delete(KnowledgeValidationItem).where(KnowledgeValidationItem.report_id == report.report_id)
            )

        report.status = status
        report.decision = "auto_accepted" if status == "auto_accepted" else "pending"
        report.new_count = int(summary.get("new_count") or 0)
        report.changed_count = int(summary.get("changed_count") or 0)
        report.removed_count = int(summary.get("removed_count") or 0)
        report.conflict_count = int(summary.get("conflict_count") or 0)
        report.inconclusive = bool(summary.get("inconclusive"))
        report.summary = summary
        report.failure_message = None
        report.completed_at = utc_now_naive()
        report.updated_at = utc_now_naive()
        for field, value in (report_metadata or {}).items():
            if hasattr(report, field):
                setattr(report, field, value)

        records = []
        for item_index, item in enumerate(items):
            digest = hashlib.sha256(
                f"{report.report_id}|{item_index}|{item['change_type']}|{item['fact_key']}".encode()
            ).hexdigest()[:32]
            record = KnowledgeValidationItem(
                item_id=f"validation_item_{digest}",
                report_id=report.report_id,
                item_index=item_index,
                change_type=item["change_type"],
                severity=item["severity"],
                decision=item.get("decision") or "pending",
                fact_key=item["fact_key"],
                relation=item.get("relation"),
                old_fact=item.get("old_fact"),
                new_fact=item.get("new_fact"),
                old_evidence=item.get("old_evidence"),
                new_evidence=item.get("new_evidence"),
                review_required=bool(item.get("review_required")),
                reason=item.get("reason"),
            )
            session.add(record)
            records.append(record)
        await session.flush()
        return report, records

    async def record_failure(
        self,
        *,
        report_id: str,
        kb_id: str,
        logical_document_id: str,
        old_file_id: str,
        candidate_file_id: str,
        failure_message: str,
        session: AsyncSession,
    ) -> KnowledgeValidationReport:
        result = await session.execute(
            select(KnowledgeValidationReport)
            .where(KnowledgeValidationReport.candidate_file_id == candidate_file_id)
            .with_for_update()
        )
        report = result.scalar_one_or_none()
        if report is None:
            report = KnowledgeValidationReport(
                report_id=report_id,
                kb_id=kb_id,
                logical_document_id=logical_document_id,
                old_file_id=old_file_id,
                candidate_file_id=candidate_file_id,
            )
            session.add(report)
        else:
            await session.execute(
                delete(KnowledgeValidationItem).where(KnowledgeValidationItem.report_id == report.report_id)
            )
        report.status = "failed"
        report.decision = "pending"
        report.inconclusive = True
        report.summary = None
        report.failure_message = failure_message
        report.updated_at = utc_now_naive()
        await session.flush()
        return report

    async def get_by_candidate(self, *, kb_id: str, candidate_file_id: str) -> KnowledgeValidationReport | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeValidationReport).where(
                    KnowledgeValidationReport.kb_id == kb_id,
                    KnowledgeValidationReport.candidate_file_id == candidate_file_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_by_candidates(self, *, kb_id: str, candidate_file_ids: list[str]) -> list[KnowledgeValidationReport]:
        if not candidate_file_ids:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeValidationReport).where(
                    KnowledgeValidationReport.kb_id == kb_id,
                    KnowledgeValidationReport.candidate_file_id.in_(candidate_file_ids),
                )
            )
            return list(result.scalars().all())

    async def get_by_report_id(self, *, report_id: str) -> KnowledgeValidationReport | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeValidationReport).where(KnowledgeValidationReport.report_id == report_id)
            )
            return result.scalar_one_or_none()

    async def list_items(self, *, report_id: str) -> list[KnowledgeValidationItem]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeValidationItem)
                .where(KnowledgeValidationItem.report_id == report_id)
                .order_by(KnowledgeValidationItem.item_index.asc())
            )
            return list(result.scalars().all())

    async def mark_published(
        self,
        *,
        report_id: str,
        published_at,
        session: AsyncSession,
    ) -> KnowledgeValidationReport:
        result = await session.execute(
            select(KnowledgeValidationReport).where(KnowledgeValidationReport.report_id == report_id).with_for_update()
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise ValueError("验证报告不存在")
        if report.status not in {"auto_accepted", "accepted"}:
            raise ValueError("验证报告尚未接受，不能发布")
        report.status = "published"
        report.published_at = published_at
        report.updated_at = utc_now_naive()
        await session.flush()
        return report

    async def set_decision(
        self,
        *,
        report_id: str,
        decision: str,
        operator_id: str,
        session: AsyncSession,
    ) -> KnowledgeValidationReport:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("验证报告决策必须是 accepted 或 rejected")
        result = await session.execute(
            select(KnowledgeValidationReport).where(KnowledgeValidationReport.report_id == report_id).with_for_update()
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise ValueError("验证报告不存在")
        if report.status != "review_required":
            raise ValueError("验证报告当前不可审核")
        report.status = decision
        report.decision = decision
        report.reviewed_by = operator_id
        report.reviewed_at = utc_now_naive()
        report.updated_at = utc_now_naive()
        await session.flush()
        return report
