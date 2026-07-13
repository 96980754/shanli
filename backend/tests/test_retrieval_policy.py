from pathlib import Path

import pytest

from app.services.retrieval_policy import RetrievalPolicy


def write_policy(tmp_path: Path, formula: str = "0.75, 0.10, 0.10, 0.05") -> Path:
    similarity, type_ratio, product_ratio, priority_ratio = formula.split(", ")
    path = tmp_path / "policy.yaml"
    path.write_text(
        f"""
type_weight:
  WP: 1.0
  DG: 0.8
  UM: 0.9
  OTH: 0.3
product_weight:
  MC: 1.0
  MS: 0.9
  GEN: 0.8
priority_boost:
  P0: 1.2
  P1: 1.0
  P2: 0.8
formula:
  similarity_ratio: {similarity}
  type_ratio: {type_ratio}
  product_ratio: {product_ratio}
  priority_ratio: {priority_ratio}
top_k:
  initial: 100
  after_rerank: 20
  final: 10
""",
        encoding="utf-8",
    )
    return path


def test_retrieval_policy_loads_weights_fallbacks_and_top_k(tmp_path: Path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))

    assert policy.type_weight("UM") == 0.9
    assert policy.type_weight("unknown") == 0.3
    assert policy.product_weight("unknown") == 0.8
    assert policy.priority_boost("unknown") == 0.8
    assert policy.top_k.initial == 100
    assert policy.top_k.after_rerank == 20
    assert policy.top_k.final == 10


@pytest.mark.parametrize(
    "yaml_body",
    [
        """\ntype_weight: {OTH: 0.3}\nproduct_weight: {GEN: 0.8}\npriority_boost: {P2: 0.8}\nformula: {similarity_ratio: 0.5, type_ratio: 0.5, product_ratio: 0.5, priority_ratio: 0.5}\ntop_k: {initial: 100, after_rerank: 20, final: 10}\n""",
        """\ntype_weight: {OTH: 0.3}\nproduct_weight: {GEN: 0.8}\npriority_boost: {P2: 0.8}\nformula: {similarity_ratio: -0.1, type_ratio: 0.5, product_ratio: 0.3, priority_ratio: 0.3}\ntop_k: {initial: 100, after_rerank: 20, final: 10}\n""",
        """\ntype_weight: {OTH: 0.3}\nproduct_weight: {GEN: 0.8}\npriority_boost: {P2: 0.8}\nformula: {similarity_ratio: 0.75, type_ratio: 0.10, product_ratio: 0.10, priority_ratio: 0.05}\ntop_k: {initial: 100, after_rerank: 20}\n""",
    ],
)
def test_retrieval_policy_rejects_invalid_configuration(tmp_path: Path, yaml_body: str):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml_body, encoding="utf-8")

    with pytest.raises(ValueError):
        RetrievalPolicy.load(path)


def test_policy_rerank_only_boosts_documents_matching_detected_product(tmp_path: Path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))

    assert policy.detect_products("MCSTARS 支持什么部署方式？") == {"MC"}
    ranked = policy.rerank(
        [
            {"chunk_id": "mc", "score": 0.91, "document_type": "DG", "product": "MC", "priority": "P0"},
            {"chunk_id": "ms", "score": 0.93, "document_type": "DG", "product": "MS", "priority": "P0"},
        ],
        matched_products={"MC"},
    )

    assert ranked[0]["chunk_id"] == "mc"
    assert ranked[0]["metadata_score"] > ranked[1]["metadata_score"]


def test_policy_does_not_apply_product_boost_without_product_alias(tmp_path: Path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))

    assert policy.detect_products("支持什么部署方式？") == set()
    ranked = policy.rerank(
        [
            {"chunk_id": "first", "score": 0.91, "document_type": "DG", "product": "MC", "priority": "P0"},
            {"chunk_id": "second", "score": 0.91, "document_type": "DG", "product": "MS", "priority": "P0"},
        ],
        matched_products=set(),
    )

    assert ranked[0]["chunk_id"] == "first"
    assert ranked[0]["metadata_score"] == ranked[1]["metadata_score"]


def test_policy_normalizes_equal_non_negative_scores_to_one(tmp_path: Path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))

    ranked = policy.rerank(
        [
            {"chunk_id": "first", "score": 0.0, "document_type": "OTH", "product": "GEN", "priority": "P2"},
            {"chunk_id": "second", "score": 0.0, "document_type": "OTH", "product": "GEN", "priority": "P2"},
        ],
        matched_products=set(),
    )

    assert all(item["normalized_similarity"] == 1.0 for item in ranked)
