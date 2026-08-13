"""KnowledgeFileRepository 写字段白名单回归测试。

背景：`_writable_fields` 曾漏收 enrichment 系列列，`_sanitize_data` 据此把
`update_enrichment_fields_with_version` 写入的 `enrichment_status`/`enrichment_data` 等
全部静默丢弃，导致信息增强“生成成功”但内容永远不落库（version 靠 SQL 表达式递增、
其余字段全空）。此处直接锁定白名单，防止再次漏加。
"""

from __future__ import annotations

from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository


def test_sanitize_data_keeps_enrichment_fields():
    data = {
        "enrichment_status": "ready",
        "enrichment_version": 3,
        "enrichment_data": {"summary": {"text": "摘要"}},
        "enrichment_content_hash": "abc123",
        "enrichment_generated_at": "2026-08-14T00:00:00",
        "enrichment_error": None,
        "enrichment_possibly_outdated": False,
        "filename": "doc.md",
    }
    sanitized = KnowledgeFileRepository._sanitize_data(data)
    for key in (
        "enrichment_status",
        "enrichment_version",
        "enrichment_data",
        "enrichment_content_hash",
        "enrichment_generated_at",
        "enrichment_error",
        "enrichment_possibly_outdated",
    ):
        assert key in sanitized, f"{key} 被 _writable_fields 过滤，信息增强无法落库"
    assert sanitized["enrichment_status"] == "ready"
    assert sanitized["filename"] == "doc.md"
