import pytest

from yuxi.knowledge.document_qa import (
    QAValidationError,
    normalize_and_validate_qa,
    normalize_question,
)


CHUNKS = {
    "chunk-1": "Shanli 2.1 支持向量检索，默认批次大小为 40。",
    "chunk-2": "管理接口位于 /api/knowledge，并支持人工确认。",
}


def test_qa_requires_valid_source_chunks_and_evidence():
    with pytest.raises(QAValidationError, match="来源"):
        normalize_and_validate_qa(
            {
                "question": "Shanli 支持什么检索？",
                "answer": "支持向量检索。",
                "source_chunk_ids": ["other-file-chunk"],
                "evidence": [{"chunk_id": "other-file-chunk", "text": "支持向量检索"}],
            },
            CHUNKS,
        )

    with pytest.raises(QAValidationError, match="证据"):
        normalize_and_validate_qa(
            {
                "question": "Shanli 支持什么检索？",
                "answer": "支持向量检索。",
                "source_chunk_ids": ["chunk-1"],
                "evidence": [],
            },
            CHUNKS,
        )


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("默认批次大小为 80。", "数字"),
        ("接口位于 https://private.example/v1。", "链接"),
        ("产品型号是 AB-900。", "型号"),
    ],
)
def test_qa_rejects_facts_missing_from_evidence(answer, message):
    with pytest.raises(QAValidationError, match=message):
        normalize_and_validate_qa(
            {
                "question": "相关配置是什么？",
                "answer": answer,
                "source_chunk_ids": ["chunk-1"],
                "evidence": [{"chunk_id": "chunk-1", "text": CHUNKS["chunk-1"]}],
            },
            CHUNKS,
        )


def test_qa_normalizes_question_for_deduplication():
    assert normalize_question("  Shanli  支持什么？ ") == normalize_question("shanli 支持什么?")


def test_valid_qa_keeps_chunk_bound_evidence():
    result = normalize_and_validate_qa(
        {
            "question": "Shanli 2.1 支持什么检索？",
            "answer": "Shanli 2.1 支持向量检索，默认批次大小为 40。",
            "source_chunk_ids": ["chunk-1"],
            "evidence": [{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
        },
        CHUNKS,
    )

    assert result["source_chunk_ids"] == ["chunk-1"]
    assert result["evidence"][0]["chunk_id"] == "chunk-1"
