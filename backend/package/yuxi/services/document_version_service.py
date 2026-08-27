from __future__ import annotations

from typing import Any

from yuxi.knowledge.graphs.milvus_graph_service import GRAPH_CONFIG_KEY, MilvusGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.knowledge_validation_repository import KnowledgeValidationRepository
from yuxi.services.document_change_analysis_service import analyze_document_changes
from yuxi.services.document_conflict_service import (
    KnowledgeConflictRepository,
    analyze_document_conflicts,
    load_conflict_ontology,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeFile


class DocumentVersionService:
    def __init__(self) -> None:
        self.file_repo = KnowledgeFileRepository()
        self.conflict_repo = KnowledgeConflictRepository()
        self.validation_repo = KnowledgeValidationRepository()
        self.kb_repo = KnowledgeBaseRepository()

    async def create_candidate(
        self,
        *,
        kb_id: str,
        current_file_id: str,
        uploaded: dict[str, Any],
        operator_id: str,
    ) -> KnowledgeFile:
        current = await self.file_repo.get_by_file_id(current_file_id)
        if current is None or current.kb_id != kb_id:
            raise ValueError("当前文档不存在")
        content_hash = str(uploaded.get("content_hash") or "").strip()
        if not content_hash:
            raise ValueError("缺少新文件 content_hash")
        if current.content_hash == content_hash:
            raise ValueError("SAME_CONTENT")

        candidate_data = {
            "file_id": uploaded["file_id"],
            "parent_id": current.parent_id,
            "filename": uploaded.get("filename") or current.filename,
            "original_filename": uploaded.get("original_filename") or current.original_filename,
            "file_type": uploaded.get("file_type") or current.file_type,
            "path": uploaded.get("path"),
            "minio_url": uploaded.get("minio_url") or uploaded.get("path"),
            "status": "uploaded",
            "content_hash": content_hash,
            "file_size": uploaded.get("file_size"),
            "content_type": current.content_type or "file",
            "processing_params": uploaded.get("processing_params") or current.processing_params or {},
            "is_folder": False,
            "created_by": operator_id,
            "updated_by": operator_id,
        }
        async with pg_manager.get_async_session_context() as session:
            return await self.file_repo.create_candidate_version(
                kb_id=kb_id,
                current_file_id=current_file_id,
                data=candidate_data,
                session=session,
            )

    async def process_candidate(
        self,
        *,
        kb_id: str,
        candidate_file_id: str,
        operator_id: str,
        context=None,
    ) -> dict[str, Any]:
        candidate = await self._get_candidate(kb_id, candidate_file_id)
        old_file_id = str(candidate.supersedes_file_id or "")
        if not old_file_id:
            raise ValueError("候选版本缺少上一版本")

        if context is not None:
            await context.set_progress(10, "解析候选版本")
        await knowledge_base.parse_file(kb_id, candidate_file_id, operator_id=operator_id)
        if context is not None:
            await context.set_progress(35, "候选版本入库")
        await knowledge_base.index_file(kb_id, candidate_file_id, operator_id=operator_id)
        await self.file_repo.update_fields(
            file_id=candidate_file_id,
            kb_id=kb_id,
            data={"status": "validation_processing", "updated_by": operator_id},
        )

        analysis = await self.analyze_changes(
            kb_id=kb_id,
            old_file_id=old_file_id,
            candidate_file_id=candidate_file_id,
            context=context,
        )
        if analysis["status"] == "failed":
            await self.file_repo.update_fields(
                file_id=candidate_file_id,
                kb_id=kb_id,
                data={"status": "validation_failed", "error_message": analysis["message"], "updated_by": operator_id},
            )
            return analysis | {"activated": False}
        if analysis["status"] == "review_required":
            await self.file_repo.update_fields(
                file_id=candidate_file_id,
                kb_id=kb_id,
                data={
                    "status": "validation_review",
                    "error_message": analysis["summary"].get("message"),
                    "updated_by": operator_id,
                },
            )
            return analysis | {"activated": False}
        if analysis["status"] != "auto_accepted":
            message = f"未知知识变更分析结果: {analysis['status']}"
            await self.file_repo.update_fields(
                file_id=candidate_file_id,
                kb_id=kb_id,
                data={"status": "validation_failed", "error_message": message, "updated_by": operator_id},
            )
            return {"status": "failed", "items": [], "message": message, "activated": False}

        await self.file_repo.update_fields(
            file_id=candidate_file_id,
            kb_id=kb_id,
            data={"status": "validation_accepted", "error_message": None, "updated_by": operator_id},
        )
        activation = await self.activate_candidate(
            kb_id=kb_id,
            candidate_file_id=candidate_file_id,
            expected_current_file_id=old_file_id,
            operator_id=operator_id,
            accept_conflicts=False,
        )
        return analysis | activation

    async def analyze_changes(
        self,
        *,
        kb_id: str,
        old_file_id: str,
        candidate_file_id: str,
        context=None,
    ) -> dict[str, Any]:
        candidate = await self._get_candidate(kb_id, candidate_file_id)
        old_file = await self.file_repo.get_by_file_id(old_file_id)
        report_id = f"validation_{candidate_file_id}"
        try:
            kb = await self.kb_repo.get_by_kb_id(kb_id)
            config = ((kb.additional_params or {}).get(GRAPH_CONFIG_KEY) if kb else None) or {}
            base_metadata = {
                "old_filename": getattr(old_file, "filename", None),
                "old_document_version": getattr(old_file, "document_version", None),
                "candidate_filename": candidate.filename,
                "candidate_document_version": candidate.document_version,
                "extraction_schema_version": 2,
            }
            if not config.get("locked"):
                # 未配置图谱时没有可比对的断言，跳过变更分析并自动启用新版
                # （对齐 changelog 设计：未配置图谱时则明确提示已跳过冲突检测并自动启用新版；
                # 与 detect_conflicts 的 not_configured 语义一致，不应让版本更新硬失败）。
                return await self._record_skipped_analysis(
                    kb_id=kb_id,
                    report_id=report_id,
                    candidate=candidate,
                    old_file_id=old_file_id,
                    candidate_file_id=candidate_file_id,
                    base_metadata=base_metadata,
                )
            graph_service = MilvusGraphService()
            if context is not None:
                await context.set_progress(65, "抽取新旧版本知识断言")
            old_chunks = await graph_service.extract_file_chunks(kb_id, old_file_id)
            new_chunks = await graph_service.extract_file_chunks(kb_id, candidate_file_id)
            extractor_options = config.get("extractor_options") or {}
            ontology = load_conflict_ontology(extractor_options)
            analysis = analyze_document_changes(old_chunks, new_chunks, ontology)
            metadata = {
                **base_metadata,
                "ontology_registry_id": extractor_options.get("ontology_registry_id"),
                "ontology_version": extractor_options.get("ontology_version"),
                "ontology_digest": extractor_options.get("ontology_digest"),
            }
            async with pg_manager.get_async_session_context() as session:
                report, _ = await self.validation_repo.replace_for_candidate(
                    report_id=report_id,
                    kb_id=kb_id,
                    logical_document_id=str(candidate.logical_document_id),
                    old_file_id=old_file_id,
                    candidate_file_id=candidate_file_id,
                    status=analysis["status"],
                    summary=analysis["summary"],
                    items=analysis["items"],
                    session=session,
                    report_metadata=metadata,
                )
                legacy_conflicts = [
                    {
                        "conflict_type": "knowledge_assertion_conflict",
                        "conflict_key": item["fact_key"],
                        "old_fact": item.get("old_fact") or {},
                        "new_fact": item.get("new_fact") or {},
                    }
                    for item in analysis["items"]
                    if item["change_type"] == "conflict" and item.get("old_fact") and item.get("new_fact")
                ]
                await self.conflict_repo.replace_for_candidate(
                    kb_id=kb_id,
                    logical_document_id=str(candidate.logical_document_id),
                    old_file_id=old_file_id,
                    new_file_id=candidate_file_id,
                    conflicts=legacy_conflicts,
                    session=session,
                )
            return analysis | {"report_id": report.report_id}
        except Exception as exc:
            async with pg_manager.get_async_session_context() as session:
                await self.validation_repo.record_failure(
                    report_id=report_id,
                    kb_id=kb_id,
                    logical_document_id=str(candidate.logical_document_id),
                    old_file_id=old_file_id,
                    candidate_file_id=candidate_file_id,
                    failure_message=str(exc),
                    session=session,
                )
            return {"status": "failed", "items": [], "message": str(exc), "report_id": report_id}

    async def _record_skipped_analysis(
        self,
        *,
        kb_id: str,
        report_id: str,
        candidate: KnowledgeFile,
        old_file_id: str,
        candidate_file_id: str,
        base_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """未配置知识图谱时跳过变更分析：写一条 auto_accepted 报告并自动启用新版。

        没有图谱就没有可比对的断言，跳过是预期行为（对齐 changelog 设计：
        “未配置图谱时则明确提示已跳过冲突检测并自动启用新版”）。激活路径只在图谱
        locked 时才发布文件级图谱，因此这里不产生任何图谱副作用。
        """
        summary = {
            "item_count": 0,
            "new_count": 0,
            "changed_count": 0,
            "removed_count": 0,
            "conflict_count": 0,
            "inconclusive": False,
            "skip_reason": "graph_not_configured",
            "message": "知识库未配置知识图谱，已跳过知识变更分析与冲突检测，自动启用新版",
        }
        analysis = {"status": "auto_accepted", "items": [], "summary": summary}
        async with pg_manager.get_async_session_context() as session:
            report, _ = await self.validation_repo.replace_for_candidate(
                report_id=report_id,
                kb_id=kb_id,
                logical_document_id=str(candidate.logical_document_id),
                old_file_id=old_file_id,
                candidate_file_id=candidate_file_id,
                status="auto_accepted",
                summary=summary,
                items=[],
                session=session,
                report_metadata=base_metadata,
            )
            await self.conflict_repo.replace_for_candidate(
                kb_id=kb_id,
                logical_document_id=str(candidate.logical_document_id),
                old_file_id=old_file_id,
                new_file_id=candidate_file_id,
                conflicts=[],
                session=session,
            )
        return analysis | {"report_id": report.report_id}

    async def detect_conflicts(
        self,
        *,
        kb_id: str,
        old_file_id: str,
        candidate_file_id: str,
        context=None,
    ) -> dict[str, Any]:
        kb = await self.kb_repo.get_by_kb_id(kb_id)
        config = ((kb.additional_params or {}).get(GRAPH_CONFIG_KEY) if kb else None) or {}
        if not config.get("locked"):
            return {"status": "not_configured", "conflicts": [], "message": "未启用知识冲突检测"}

        try:
            graph_service = MilvusGraphService()
            if context is not None:
                await context.set_progress(65, "抽取新旧版本结构化事实")
            old_chunks = await graph_service.extract_file_chunks(kb_id, old_file_id)
            new_chunks = await graph_service.extract_file_chunks(kb_id, candidate_file_id)
            extractor_options = config.get("extractor_options") or {}
            ontology = load_conflict_ontology(extractor_options)
            analysis = analyze_document_conflicts(old_chunks, new_chunks, ontology)
            conflicts = analysis["conflicts"]
            candidate = await self._get_candidate(kb_id, candidate_file_id)
            async with pg_manager.get_async_session_context() as session:
                await self.conflict_repo.replace_for_candidate(
                    kb_id=kb_id,
                    logical_document_id=str(candidate.logical_document_id),
                    old_file_id=old_file_id,
                    new_file_id=candidate_file_id,
                    conflicts=conflicts,
                    session=session,
                )
            return {
                **analysis,
                "conflict_count": len(conflicts),
            }
        except Exception as exc:
            return {"status": "failed", "conflicts": [], "message": str(exc)}

    async def activate_candidate(
        self,
        *,
        kb_id: str,
        candidate_file_id: str,
        expected_current_file_id: str,
        operator_id: str,
        accept_conflicts: bool,
    ) -> dict[str, Any]:
        await self._get_candidate(kb_id, candidate_file_id)
        report = await self.validation_repo.get_by_candidate(kb_id=kb_id, candidate_file_id=candidate_file_id)
        if report is None:
            raise ValueError("候选版本尚未完成知识变更分析")
        if report.status == "review_required":
            if not accept_conflicts:
                raise ValueError("CONFLICT_REVIEW_REQUIRED")
        elif report.status not in {"auto_accepted", "accepted"}:
            raise ValueError("候选版本尚未完成知识变更分析")

        graph_service = None
        kb = await self.kb_repo.get_by_kb_id(kb_id)
        graph_config = ((kb.additional_params or {}).get(GRAPH_CONFIG_KEY) if kb else None) or {}
        if graph_config.get("locked"):
            graph_service = MilvusGraphService()
            await graph_service.publish_file_graph(kb_id, candidate_file_id)

        try:
            async with pg_manager.get_async_session_context() as session:
                old, current = await self.file_repo.activate_candidate(
                    kb_id=kb_id,
                    candidate_file_id=candidate_file_id,
                    expected_current_file_id=expected_current_file_id,
                    operator_id=operator_id,
                    session=session,
                )
                if report.status == "review_required":
                    report = await self.validation_repo.set_decision(
                        report_id=report.report_id,
                        decision="accepted",
                        operator_id=operator_id,
                        session=session,
                    )
                    await self.conflict_repo.accept_candidate(
                        new_file_id=candidate_file_id,
                        operator_id=operator_id,
                        session=session,
                    )
                await self.validation_repo.mark_published(
                    report_id=report.report_id,
                    published_at=current.activated_at,
                    session=session,
                )
        except Exception:
            if graph_service is not None:
                await graph_service.delete_file_graph(kb_id, candidate_file_id)
                await graph_service.chunk_repo.reset_graph_state_by_file_id(candidate_file_id)
            raise

        cleanup_warnings = await self._cleanup_old_version(kb_id, old)
        return {
            "activated": True,
            "current_file_id": current.file_id,
            "archived_file_id": old.file_id,
            "cleanup_warnings": cleanup_warnings,
        }

    async def reject_candidate(
        self,
        *,
        kb_id: str,
        report_id: str,
        operator_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        report = await self.validation_repo.get_by_report_id(report_id=report_id)
        if report is None or report.kb_id != kb_id:
            raise ValueError("验证报告不存在")
        candidate = await self._get_candidate(kb_id, report.candidate_file_id)
        async with pg_manager.get_async_session_context() as session:
            report = await self.validation_repo.set_decision(
                report_id=report_id,
                decision="rejected",
                operator_id=operator_id,
                session=session,
            )
            if reason:
                summary = dict(report.summary or {})
                summary["rejection_reason"] = reason
                report.summary = summary
            candidate.status = "validation_rejected"
            candidate.error_message = reason
            candidate.updated_by = operator_id
        return {"rejected": True, "report_id": report_id, "candidate_file_id": candidate.file_id}

    async def _cleanup_old_version(self, kb_id: str, old_file: KnowledgeFile) -> list[str]:
        warnings: list[str] = []
        try:
            kb_instance = await knowledge_base._get_kb_for_database(kb_id)
            warnings.extend(await kb_instance.archive_file_indexes(kb_id, old_file.file_id))
        except Exception as exc:
            warnings.append(f"旧版本活动索引清理失败: {exc}")
        try:
            from yuxi.knowledge.utils.mindmap_utils import remove_file_from_mindmap

            await remove_file_from_mindmap(kb_id, old_file.file_id, old_file.filename)
        except Exception as exc:
            warnings.append(f"思维导图清理失败: {exc}")
        return warnings

    async def _get_candidate(self, kb_id: str, candidate_file_id: str) -> KnowledgeFile:
        candidate = await self.file_repo.get_by_file_id(candidate_file_id)
        if candidate is None or candidate.kb_id != kb_id or candidate.is_current:
            raise ValueError("候选版本不存在")
        return candidate
