from __future__ import annotations
import secrets
from types import SimpleNamespace
from typing import Any
from yuxi import config
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.document_qa import (
    DocumentQAGenerator,
    QAProviderUnavailable,
    normalize_and_validate_qa,
)
from yuxi.knowledge.enrichment import formal_content_hash
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.utils import (
    is_minio_url,
    parse_minio_url,
    resolve_processing_params,
    sanitize_processing_error,
)
from yuxi.repositories.document_qa_repository import DocumentQARepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive

DRAFT_QA_STATUS = FileStatus.WAITING_CONFIRMATION
INDEXED_QA_STATUSES = {FileStatus.INDEXED, FileStatus.ERROR_REPLACEMENT_CLEANUP}


class DocumentQAError(ValueError):
    """User-visible document QA domain error."""


class QANotFound(DocumentQAError):
    """Raised without disclosing cross-knowledge-base objects."""


class QAVersionConflict(DocumentQAError):
    """Raised when a stale editor tries to update a QA pair."""


class _RuntimeQAIndexBackend:
    async def upsert_confirmed_qa(self, **payload) -> None:
        kb = await knowledge_base.aget_kb(payload["kb_id"])
        await kb.upsert_confirmed_qa(**payload)

    async def delete_confirmed_qa(self, kb_id: str, qa_id: str) -> None:
        kb = await knowledge_base.aget_kb(kb_id)
        await kb.delete_confirmed_qa(kb_id, qa_id)


class DocumentQAService:
    def __init__(
        self,
        *,
        file_repository: KnowledgeFileRepository | None = None,
        chunk_repository: KnowledgeChunkRepository | None = None,
        qa_repository: DocumentQARepository | None = None,
        generator: DocumentQAGenerator | None = None,
        index_backend: Any | None = None,
    ):
        self.file_repository = file_repository or KnowledgeFileRepository()
        self.chunk_repository = chunk_repository or KnowledgeChunkRepository()
        self.qa_repository = qa_repository or DocumentQARepository()
        self.generator = generator or DocumentQAGenerator()
        self.index_backend = index_backend or _RuntimeQAIndexBackend()

    async def _get_file(self, kb_id: str, file_id: str):
        record = await self.file_repository.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id or record.is_folder:
            raise QANotFound("文档不存在")
        return record

    @staticmethod
    def _assert_eligible(record) -> None:
        if not record.is_active or record.status not in INDEXED_QA_STATUSES or not record.markdown_file:
            raise QANotFound("文档不是当前可检索的正式版本")

    @staticmethod
    async def _read_markdown(path: str | None) -> str:
        if not path or not is_minio_url(path):
            raise DocumentQAError("正式 Markdown 不可读取")
        bucket, object_name = parse_minio_url(path)
        try:
            return (await get_minio_client().adownload_file(bucket, object_name)).decode("utf-8")
        except Exception as exc:
            raise DocumentQAError("正式 Markdown 不可读取") from exc

    async def _context(self, kb_id: str, file_id: str, selected_chunk_ids: list[str] | None = None):
        record = await self._get_file(kb_id, file_id)
        if record.status == DRAFT_QA_STATUS:
            chunks, content_hash = await self._draft_chunks(record)
        else:
            self._assert_eligible(record)
            chunks = await self.chunk_repository.list_by_file_id(file_id)
            markdown = await self._read_markdown(record.markdown_file)
            content_hash = formal_content_hash(markdown)
        if selected_chunk_ids:
            selected = set(selected_chunk_ids)
            chunks = [chunk for chunk in chunks if chunk.chunk_id in selected]
            if len(chunks) != len(selected):
                raise DocumentQAError("选中的来源 chunk 不属于当前文档")
        if not chunks:
            raise DocumentQAError("文档没有可用于 QA 的文本片段")
        return record, chunks, content_hash

    async def _draft_chunks(self, record):
        """Split the pending cleaning draft with the same deterministic chunker used at indexing."""
        if not getattr(record, "cleaning_draft_file", None):
            raise DocumentQAError("文档尚未生成清洗草稿")
        markdown = await self._read_markdown(record.cleaning_draft_file)
        kb_additional_params = None
        try:
            kb = await knowledge_base.aget_kb(record.kb_id)
            kb_additional_params = (kb.databases_meta or {}).get(record.kb_id, {}).get("metadata")
        except Exception:  # noqa: BLE001 - kb metadata is only a fallback for draft chunking
            kb_additional_params = None
        params = resolve_processing_params(
            kb_additional_params=kb_additional_params,
            file_processing_params=getattr(record, "processing_params", None),
        )
        chunk_records = chunk_markdown(
            markdown,
            record.file_id,
            getattr(record, "filename", "") or "",
            params,
        )
        chunks = [
            SimpleNamespace(chunk_id=chunk["chunk_id"], content=chunk["content"])
            for chunk in chunk_records
            if chunk.get("content")
        ]
        if not chunks:
            raise DocumentQAError("当前清洗文本无法生成可用于 QA 的文本片段")
        return chunks, formal_content_hash(markdown)

    @staticmethod
    def _public(record, *, idempotent: bool = False) -> dict[str, Any]:
        return {
            "qa_id": record.qa_id,
            "kb_id": record.kb_id,
            "file_id": record.file_id,
            "question": record.question,
            "answer": record.answer,
            "source_chunk_ids": list(record.source_chunk_ids or []),
            "evidence": list(record.evidence or []),
            "source": record.source,
            "status": record.status,
            "sync_status": record.sync_status,
            "sync_error": getattr(record, "sync_error", None),
            "version": int(record.version or 1),
            "cleaning_version": int(record.cleaning_version or 0),
            "content_hash": record.content_hash,
            "model_name": getattr(record, "model_name", None),
            "model_version": getattr(record, "model_version", None),
            "generated_at": (utc_isoformat(record.generated_at) if getattr(record, "generated_at", None) else None),
            "updated_at": utc_isoformat(record.updated_at) if getattr(record, "updated_at", None) else None,
            "updated_by": getattr(record, "updated_by", None),
            "confirmed_at": (utc_isoformat(record.confirmed_at) if getattr(record, "confirmed_at", None) else None),
            "confirmed_by": getattr(record, "confirmed_by", None),
            "possibly_outdated": bool(getattr(record, "possibly_outdated", False)),
            "idempotent": idempotent,
        }

    async def list(self, *, kb_id: str, file_id: str) -> dict[str, Any]:
        record = await self._get_file(kb_id, file_id)
        items = await self.qa_repository.list_by_file_id(kb_id=kb_id, file_id=file_id)
        return {
            "file_id": file_id,
            "cleaning_version": int(record.cleaning_version or 0),
            "draft_mode": record.status == DRAFT_QA_STATUS,
            "confirmable": bool(record.status in INDEXED_QA_STATUSES and int(record.chunk_count or 0) > 0),
            "items": [self._public(item) for item in items],
        }

    async def get(self, *, kb_id: str, file_id: str, qa_id: str) -> dict[str, Any]:
        await self._get_file(kb_id, file_id)
        record = await self.qa_repository.get_by_qa_id(qa_id)
        if record is None or record.kb_id != kb_id or record.file_id != file_id:
            raise QANotFound("QA 不存在")
        return self._public(record)

    async def generate_drafts(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        selected_chunk_ids: list[str] | None = None,
        replace_generated: bool = False,
    ) -> dict[str, Any]:
        record, chunks, content_hash = await self._context(kb_id, file_id, selected_chunk_ids)
        cleaning_version = int(record.cleaning_version or 0)
        try:
            generated = await self.generator.generate(
                chunks,
                model_spec=config.document_qa_model or record.__dict__.get("llm_model_spec") or config.default_model,
                temperature=float(config.document_qa_temperature),
                timeout_seconds=float(config.document_qa_timeout_seconds),
                attempts=int(config.document_qa_output_attempts),
                max_pairs=int(config.document_qa_max_pairs_per_document),
                question_max_chars=int(config.document_qa_question_max_chars),
                answer_max_chars=int(config.document_qa_answer_max_chars),
            )
        except QAProviderUnavailable:
            return {"file_id": file_id, "status": "skipped", "items": []}
        latest = await self._get_file(kb_id, file_id)
        latest_source = (
            getattr(latest, "cleaning_draft_file", None) if latest.status == DRAFT_QA_STATUS else latest.markdown_file
        )
        latest_markdown = await self._read_markdown(latest_source)
        if (
            not latest.is_active
            or int(latest.cleaning_version or 0) != cleaning_version
            or formal_content_hash(latest_markdown) != content_hash
        ):
            raise QAVersionConflict("正文版本已变化，旧生成任务不能保存结果")
        now = utc_now_naive()
        saved = []
        source_chunks = {chunk.chunk_id: chunk.content for chunk in chunks}
        per_chunk_counts: dict[str, int] = {}
        for generated_item in generated:
            item = {
                **normalize_and_validate_qa(
                    generated_item,
                    source_chunks,
                    question_max_chars=int(config.document_qa_question_max_chars),
                    answer_max_chars=int(config.document_qa_answer_max_chars),
                ),
                "model_name": generated_item.get("model_name"),
                "model_version": generated_item.get("model_version"),
            }
            if any(
                per_chunk_counts.get(chunk_id, 0) >= int(config.document_qa_max_pairs_per_chunk)
                for chunk_id in item["source_chunk_ids"]
            ):
                continue
            existing = await self.qa_repository.find_by_identity(
                file_id=file_id,
                content_hash=content_hash,
                question_hash=item["question_hash"],
            )
            if existing is not None:
                if (
                    replace_generated
                    and existing.source == "generated"
                    and existing.status == "draft"
                    and not getattr(existing, "deleted_by_user", False)
                ):
                    updated = await self.qa_repository.update_with_version(
                        kb_id=kb_id,
                        file_id=file_id,
                        qa_id=existing.qa_id,
                        expected_version=existing.version,
                        data={
                            **item,
                            "generated_at": now,
                            "updated_by": operator_id,
                            "sync_status": "pending",
                            "sync_error": None,
                        },
                    )
                    if updated:
                        saved.append(updated)
                continue
            created_record, created = await self.qa_repository.create_or_get(
                {
                    "qa_id": f"qa_{secrets.token_hex(12)}",
                    "kb_id": kb_id,
                    "file_id": file_id,
                    **item,
                    "source": "generated",
                    "status": "draft",
                    "sync_status": "pending",
                    "cleaning_version": cleaning_version,
                    "content_hash": content_hash,
                    "generated_at": now,
                    "updated_by": operator_id,
                    "possibly_outdated": False,
                    "deleted_by_user": False,
                }
            )
            if not created:
                continue
            saved.append(created_record)
            for chunk_id in item["source_chunk_ids"]:
                per_chunk_counts[chunk_id] = per_chunk_counts.get(chunk_id, 0) + 1
        return {"file_id": file_id, "status": "generated", "items": [self._public(item) for item in saved]}

    async def create_manual(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        question: str,
        answer: str,
        source_chunk_ids: list[str],
        evidence: list[dict[str, str]],
    ) -> dict[str, Any]:
        record, chunks, content_hash = await self._context(kb_id, file_id)
        validated = normalize_and_validate_qa(
            {
                "question": question,
                "answer": answer,
                "source_chunk_ids": source_chunk_ids,
                "evidence": evidence,
            },
            {chunk.chunk_id: chunk.content for chunk in chunks},
            question_max_chars=int(config.document_qa_question_max_chars),
            answer_max_chars=int(config.document_qa_answer_max_chars),
        )
        if await self.qa_repository.find_by_identity(
            file_id=file_id,
            content_hash=content_hash,
            question_hash=validated["question_hash"],
        ):
            raise DocumentQAError("当前正文版本已存在相同问题")
        record = await self.qa_repository.create(
            {
                "qa_id": f"qa_{secrets.token_hex(12)}",
                "kb_id": kb_id,
                "file_id": file_id,
                **validated,
                "source": "manual",
                "status": "draft",
                "sync_status": "pending",
                "cleaning_version": int(record.cleaning_version or 0),
                "content_hash": content_hash,
                "updated_by": operator_id,
                "possibly_outdated": False,
                "deleted_by_user": False,
            }
        )
        return self._public(record)

    async def update(
        self,
        *,
        kb_id: str,
        file_id: str,
        qa_id: str,
        operator_id: str,
        expected_version: int,
        question: str,
        answer: str,
        source_chunk_ids: list[str],
        evidence: list[dict[str, str]],
    ) -> dict[str, Any]:
        file_record, chunks, content_hash = await self._context(kb_id, file_id)
        current = await self.qa_repository.get_by_qa_id(qa_id)
        if current is None or current.kb_id != kb_id or current.file_id != file_id:
            raise QANotFound("QA 不存在")
        if int(current.version or 1) != max(1, int(expected_version)):
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        was_confirmed = current.status == "confirmed"
        validated = normalize_and_validate_qa(
            {
                "question": question,
                "answer": answer,
                "source_chunk_ids": source_chunk_ids,
                "evidence": evidence,
            },
            {chunk.chunk_id: chunk.content for chunk in chunks},
            question_max_chars=int(config.document_qa_question_max_chars),
            answer_max_chars=int(config.document_qa_answer_max_chars),
        )
        updated = await self.qa_repository.update_with_version(
            kb_id=kb_id,
            file_id=file_id,
            qa_id=qa_id,
            expected_version=expected_version,
            data={
                **validated,
                "source": "manual",
                "status": "draft",
                "sync_status": "removing" if was_confirmed else "pending",
                "sync_error": None,
                "cleaning_version": int(file_record.cleaning_version or 0),
                "content_hash": content_hash,
                "updated_by": operator_id,
                "confirmed_at": None,
                "confirmed_by": None,
                "possibly_outdated": False,
            },
        )
        if updated is None:
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        if was_confirmed:
            try:
                await self.index_backend.delete_confirmed_qa(kb_id, qa_id)
                projection = await self.qa_repository.update_with_version(
                    kb_id=kb_id,
                    file_id=file_id,
                    qa_id=qa_id,
                    expected_version=updated.version,
                    data={"sync_status": "removed", "sync_error": None},
                )
            except Exception as exc:
                projection = await self.qa_repository.update_with_version(
                    kb_id=kb_id,
                    file_id=file_id,
                    qa_id=qa_id,
                    expected_version=updated.version,
                    data={
                        "sync_status": "failed",
                        "sync_error": sanitize_processing_error(exc),
                    },
                )
            return self._public(projection or updated)
        return self._public(updated)

    async def confirm(
        self,
        *,
        kb_id: str,
        file_id: str,
        qa_id: str,
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        file_record, chunks, content_hash = await self._context(kb_id, file_id)
        current = await self.qa_repository.get_by_qa_id(qa_id)
        if current is None or current.kb_id != kb_id or current.file_id != file_id:
            raise QANotFound("QA 不存在")
        if file_record.status == DRAFT_QA_STATUS:
            raise DocumentQAError("文档尚未确认清洗入库，QA 将在清洗确认后与正式 chunks 绑定")
        if (
            current.status == "confirmed"
            and current.sync_status == "synced"
            and current.cleaning_version == file_record.cleaning_version
            and current.content_hash == content_hash
        ):
            return self._public(current, idempotent=True)
        if int(current.version or 1) != max(1, int(expected_version)):
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        if current.status == "rejected":
            raise DocumentQAError("已拒绝的 QA 不能确认")
        if current.cleaning_version != file_record.cleaning_version or current.content_hash != content_hash:
            raise QAVersionConflict("QA 对应的正文版本已变化，请重新校验")
        normalize_and_validate_qa(
            {
                "question": current.question,
                "answer": current.answer,
                "source_chunk_ids": current.source_chunk_ids,
                "evidence": current.evidence,
            },
            {chunk.chunk_id: chunk.content for chunk in chunks},
            question_max_chars=int(config.document_qa_question_max_chars),
            answer_max_chars=int(config.document_qa_answer_max_chars),
        )
        now = utc_now_naive()
        confirmed = await self.qa_repository.update_with_version(
            kb_id=kb_id,
            file_id=file_id,
            qa_id=qa_id,
            expected_version=expected_version,
            data={
                "status": "confirmed",
                "sync_status": "syncing",
                "sync_error": None,
                "confirmed_at": now,
                "confirmed_by": operator_id,
                "updated_by": operator_id,
                "possibly_outdated": False,
            },
        )
        if confirmed is None:
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        try:
            await self.index_backend.upsert_confirmed_qa(
                kb_id=kb_id,
                qa_id=qa_id,
                file_id=file_id,
                question=confirmed.question,
                answer=confirmed.answer,
            )
        except Exception as exc:
            failed = await self.qa_repository.update_with_version(
                kb_id=kb_id,
                file_id=file_id,
                qa_id=qa_id,
                expected_version=confirmed.version,
                data={"sync_status": "failed", "sync_error": sanitize_processing_error(exc)},
            )
            return self._public(failed or confirmed)
        synced = await self.qa_repository.update_with_version(
            kb_id=kb_id,
            file_id=file_id,
            qa_id=qa_id,
            expected_version=confirmed.version,
            data={"sync_status": "synced", "sync_error": None},
        )
        if synced is None:
            raise QAVersionConflict("QA 同步完成但状态已被其他用户更新")
        return self._public(synced)

    async def reject_or_delete(
        self,
        *,
        kb_id: str,
        file_id: str,
        qa_id: str,
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        current = await self.qa_repository.get_by_qa_id(qa_id)
        if current is None or current.kb_id != kb_id or current.file_id != file_id:
            raise QANotFound("QA 不存在")
        if int(current.version or 1) != max(1, int(expected_version)):
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        was_confirmed = current.status == "confirmed"
        updated = await self.qa_repository.update_with_version(
            kb_id=kb_id,
            file_id=file_id,
            qa_id=qa_id,
            expected_version=expected_version,
            data={
                "status": "rejected",
                "sync_status": "removing" if was_confirmed else "removed",
                "sync_error": None,
                "deleted_by_user": True,
                "updated_by": operator_id,
            },
        )
        if updated is None:
            raise QAVersionConflict("QA 已被其他用户更新，请刷新后重试")
        if was_confirmed:
            try:
                await self.index_backend.delete_confirmed_qa(kb_id, qa_id)
                projection = await self.qa_repository.update_with_version(
                    kb_id=kb_id,
                    file_id=file_id,
                    qa_id=qa_id,
                    expected_version=updated.version,
                    data={"sync_status": "removed", "sync_error": None},
                )
            except Exception as exc:
                projection = await self.qa_repository.update_with_version(
                    kb_id=kb_id,
                    file_id=file_id,
                    qa_id=qa_id,
                    expected_version=updated.version,
                    data={
                        "sync_status": "failed",
                        "sync_error": sanitize_processing_error(exc),
                    },
                )
            return self._public(projection or updated)
        return self._public(updated)

    async def delete_draft(
        self,
        *,
        kb_id: str,
        file_id: str,
        qa_id: str,
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        current = await self.qa_repository.get_by_qa_id(qa_id)
        if current is None or current.kb_id != kb_id or current.file_id != file_id:
            raise QANotFound("QA 不存在")
        if current.status == "confirmed":
            raise DocumentQAError("已确认 QA 不能作为草稿删除，请先显式拒绝")
        return await self.reject_or_delete(
            kb_id=kb_id,
            file_id=file_id,
            qa_id=qa_id,
            operator_id=operator_id,
            expected_version=expected_version,
        )

    async def mark_file_qas_outdated(self, *, kb_id: str, file_id: str) -> int:
        return await self.qa_repository.mark_outdated_by_file_id(kb_id=kb_id, file_id=file_id)

    async def rebase_draft_qas(self, *, kb_id: str, file_id: str, operator_id: str | None = None) -> int:
        """Rebind draft-mode QA pairs to the real chunks created after cleaning confirmation."""
        record = await self._get_file(kb_id, file_id)
        if not record.is_active or not record.markdown_file:
            raise DocumentQAError("文档没有可用的正式 Markdown")
        markdown = await self._read_markdown(record.markdown_file)
        confirmed_hash = formal_content_hash(markdown)
        chunks = await self.chunk_repository.list_by_file_id(file_id)
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        rows = await self.qa_repository.list_by_file_id(kb_id=kb_id, file_id=file_id)
        rebound = 0
        for row in rows:
            if row.status == "rejected" or row.content_hash != confirmed_hash:
                continue
            source_chunk_ids = list(row.source_chunk_ids or [])
            if all(chunk_id in chunk_by_id for chunk_id in source_chunk_ids):
                continue
            new_source_chunk_ids: list[str] = []
            new_evidence: list[dict[str, str]] = []
            unmatched = False
            for evidence in row.evidence or []:
                if not isinstance(evidence, dict):
                    continue
                chunk_id = str(evidence.get("chunk_id") or "")
                text = str(evidence.get("text") or "")
                real_chunk_id = self._find_chunk_for_evidence(chunk_by_id, text, chunk_id)
                if real_chunk_id is None:
                    unmatched = True
                    new_evidence.append({"chunk_id": chunk_id, "text": text})
                    continue
                if real_chunk_id not in new_source_chunk_ids:
                    new_source_chunk_ids.append(real_chunk_id)
                new_evidence.append({"chunk_id": real_chunk_id, "text": text})
            updated = await self.qa_repository.update_with_version(
                kb_id=kb_id,
                file_id=file_id,
                qa_id=row.qa_id,
                expected_version=row.version,
                data={
                    "source_chunk_ids": new_source_chunk_ids,
                    "evidence": new_evidence,
                    "content_hash": confirmed_hash,
                    "possibly_outdated": unmatched or row.possibly_outdated,
                    "updated_by": operator_id,
                },
            )
            if updated is not None:
                rebound += 1
        return rebound

    @staticmethod
    def _find_chunk_for_evidence(
        chunk_by_id: dict[str, Any],
        text: str,
        preferred_chunk_id: str | None = None,
    ) -> str | None:
        if not text:
            return None
        normalized = " ".join(text.split())
        if preferred_chunk_id in chunk_by_id:
            preferred_content = chunk_by_id[preferred_chunk_id].content
            if text in preferred_content or (normalized and normalized in " ".join(preferred_content.split())):
                return preferred_chunk_id
        for chunk_id, chunk in chunk_by_id.items():
            if text in chunk.content:
                return chunk_id
        for chunk_id, chunk in chunk_by_id.items():
            if normalized and normalized in " ".join(chunk.content.split()):
                return chunk_id
        return None


async def enqueue_document_qa_generation(
    *,
    kb_id: str,
    file_id: str,
    operator_id: str,
    selected_chunk_ids: list[str] | None = None,
    replace_generated: bool = False,
) -> tuple[str, bool]:
    file_record = await DocumentQAService()._get_file(kb_id, file_id)
    payload = {
        "kb_id": kb_id,
        "file_id": file_id,
        "cleaning_version": int(file_record.cleaning_version or 0),
        "selected_chunk_ids": sorted(selected_chunk_ids or []),
        "replace_generated": bool(replace_generated),
    }

    async def run_generation(context: TaskContext):
        await context.set_progress(10, "正在生成文档 QA 草稿")
        result = await DocumentQAService().generate_drafts(
            kb_id=kb_id,
            file_id=file_id,
            operator_id=operator_id,
            selected_chunk_ids=selected_chunk_ids,
            replace_generated=replace_generated,
        )
        await context.set_progress(100, "文档 QA 草稿生成完成")
        return {"kb_id": kb_id, "file_id": file_id, "status": result["status"]}

    task, created = await tasker.enqueue_unique_by_payload(
        name="生成文档 QA 草稿",
        task_type="document_qa_generation",
        payload=payload,
        payload_match=payload,
        statuses={"pending", "running"},
        coroutine=run_generation,
    )
    return task.id, created


async def enqueue_auto_document_qa(*, kb_id: str, file_id: str, operator_id: str) -> None:
    if not config.document_qa_auto_generate:
        return
    try:
        await enqueue_document_qa_generation(kb_id=kb_id, file_id=file_id, operator_id=operator_id)
    except Exception as exc:  # noqa: BLE001 - optional QA generation must not affect indexing
        logger.warning("Failed to enqueue document QA for {}: {}", file_id, sanitize_processing_error(exc))


__all__ = [
    "DocumentQAError",
    "DocumentQAService",
    "QANotFound",
    "QAVersionConflict",
    "enqueue_auto_document_qa",
    "enqueue_document_qa_generation",
]
