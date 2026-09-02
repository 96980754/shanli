from __future__ import annotations

import difflib
from typing import Any

from yuxi.knowledge.utils import is_minio_url, parse_minio_url
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.minio import get_minio_client

DIFF_CONTEXT_LINES = 3


class DocumentDiffNotFoundError(ValueError):
    """对比的版本（文件）不存在或没有可对比的正文。"""


class DocumentDiffFamilyMismatchError(ValueError):
    """两个版本不属于同一逻辑文档家族。"""


def compute_line_diff(text_a: str, text_b: str, *, context_lines: int = DIFF_CONTEXT_LINES) -> dict[str, Any]:
    """文本级逐行 diff（difflib 行级 opcodes，统一成 add/del/ctx 行流 + 统计 + hunks）。

    两个输入是规范化 markdown 全文；输出直接喂前端对比视图：
    - stats: added_lines / removed_lines / unchanged_lines（全文范围）；
    - hunks: 连续差异块，每个 hunk 的 lines 已带前后各 context_lines 行上下文，
      行号从 1 开始、base=旧版行号、target=新版行号，纯新增行无 old_no，纯删除行无 new_no；
    - identical: 无任何增删行（仅格式差异视为相同）。
    """
    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)

    added_lines = 0
    removed_lines = 0
    unchanged_lines = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged_lines += i2 - i1
        elif tag == "replace":
            removed_lines += i2 - i1
            added_lines += j2 - j1
        elif tag == "delete":
            removed_lines += i2 - i1
        elif tag == "insert":
            added_lines += j2 - j1

    hunks = []
    for group in matcher.get_grouped_opcodes(context_lines):
        lines: list[dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset in range(i2 - i1):
                    index = i1 + offset
                    lines.append(
                        {
                            "type": "ctx",
                            "old_no": index + 1,
                            "new_no": j1 + offset + 1,
                            "text": lines_a[index],
                        }
                    )
            elif tag == "delete":
                for index in range(i1, i2):
                    lines.append({"type": "del", "old_no": index + 1, "new_no": None, "text": lines_a[index]})
            elif tag == "insert":
                for index in range(j1, j2):
                    lines.append({"type": "add", "old_no": None, "new_no": index + 1, "text": lines_b[index]})
            elif tag == "replace":
                for index in range(i1, i2):
                    lines.append({"type": "del", "old_no": index + 1, "new_no": None, "text": lines_a[index]})
                for index in range(j1, j2):
                    lines.append({"type": "add", "old_no": None, "new_no": index + 1, "text": lines_b[index]})
        if not lines:
            continue
        old_nos = [item["old_no"] for item in lines if item["old_no"] is not None]
        new_nos = [item["new_no"] for item in lines if item["new_no"] is not None]
        hunks.append(
            {
                "old_start": old_nos[0] if old_nos else None,
                "old_end": old_nos[-1] if old_nos else None,
                "new_start": new_nos[0] if new_nos else None,
                "new_end": new_nos[-1] if new_nos else None,
                "lines": lines,
            }
        )

    return {
        "identical": added_lines == 0 and removed_lines == 0,
        "stats": {
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "unchanged_lines": unchanged_lines,
        },
        "hunks": hunks,
    }


class DocumentDiffService:
    def __init__(self) -> None:
        self.file_repo = KnowledgeFileRepository()
        self.chunk_repo = KnowledgeChunkRepository()

    async def diff_versions(
        self,
        *,
        kb_id: str,
        version_a_file_id: str,
        version_b_file_id: str,
    ) -> dict[str, Any]:
        """对比同一逻辑文档家族的两个版本，返回 base/target 版本元信息 + 行级 diff。

        校验：两个 file_id 都存在且属于 kb_id；属同一文档家族（同 logical_document_id，
        或经 supersedes/previous 链可归并到同家族）；都不是文件夹。文本内容严格按各自
        file_id 读取（历史版本不重定向当前版）。
        """
        file_a = await self._load_version_file(kb_id, version_a_file_id)
        file_b = await self._load_version_file(kb_id, version_b_file_id)
        await self._ensure_same_family(kb_id, file_a, file_b)

        text_a = await self._file_text(file_a)
        text_b = await self._file_text(file_b)
        diff = compute_line_diff(text_a, text_b)

        return {
            "base": self._version_meta(file_a),
            "target": self._version_meta(file_b),
            **diff,
        }

    async def get_version_text(self, *, kb_id: str, file_id: str) -> str:
        """读取指定 file_id（含历史/候选版本）的规范化正文。

        优先读该行保留的 markdown_file 解析文本；缺失/不可用则按 chunk_index 顺序拼接
        PG chunks 兜底。内容访问严格按传入 file_id，不重定向当前版本。
        """
        record = await self._load_version_file(kb_id, file_id)
        return await self._file_text(record)

    async def _file_text(self, record) -> str:
        """按已加载的文件行读正文：markdown_file 优先，PG chunks 拼接兜底。"""
        markdown_text = await self._read_parsed_markdown(record.markdown_file) if record.markdown_file else None
        if markdown_text:
            return markdown_text

        chunks = await self.chunk_repo.list_by_file_id(record.file_id)
        if not chunks:
            raise DocumentDiffNotFoundError(f"版本 {record.file_id} 没有可对比的正文内容")
        return "\n".join(chunk.content for chunk in chunks)

    async def _load_version_file(self, kb_id: str, file_id: str):
        record = await self.file_repo.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id:
            raise DocumentDiffNotFoundError(f"文件不存在: {file_id}")
        if record.is_folder:
            raise DocumentDiffNotFoundError(f"文件 {file_id} 是文件夹，不支持版本对比")
        return record

    async def _ensure_same_family(self, kb_id: str, file_a, file_b) -> None:
        if file_a.file_id == file_b.file_id:
            return
        if file_a.logical_document_id and file_b.logical_document_id:
            if file_a.logical_document_id == file_b.logical_document_id:
                return
            raise DocumentDiffFamilyMismatchError("两个版本不属于同一逻辑文档")
        # 一侧缺 logical_document_id（存量数据锚点断裂）时，走版本链判定同家族
        chain = await self.file_repo.list_versions(kb_id=kb_id, file_id=file_a.file_id)
        chain_ids = {item.file_id for item in chain}
        if file_b.file_id in chain_ids:
            return
        raise DocumentDiffFamilyMismatchError("两个版本不属于同一逻辑文档")

    @staticmethod
    async def _read_parsed_markdown(markdown_file: str) -> str | None:
        if not markdown_file or not is_minio_url(markdown_file):
            return None
        bucket_name, object_name = parse_minio_url(markdown_file)
        content_bytes = await get_minio_client().adownload_file(bucket_name, object_name)
        return content_bytes.decode("utf-8")

    @staticmethod
    def _version_meta(record) -> dict[str, Any]:
        return {
            "file_id": record.file_id,
            "document_version": record.document_version,
            "filename": record.filename,
            "original_filename": record.original_filename,
            "is_current": record.is_current,
            "status": record.status,
            "activated_at": record.activated_at,
            "logical_document_id": record.logical_document_id,
        }
