from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.knowledge.graphs.extractors import (
    GraphExtractorFactory,
    LLMGraphExtractor,
    OntologyIdentityMismatchError,
    normalize_extraction_result,
)
from yuxi.knowledge.graphs.extractors.llm import (
    MAX_CONCURRENCY_COUNT,
    MODEL_MAX_ATTEMPTS,
    MODEL_TIMEOUT_SECONDS,
)
from yuxi.knowledge.graphs.graph_utils import build_graph_payload
from yuxi.knowledge.graphs.milvus_graph_service import (
    MilvusGraphService,
    OntologySwitchRequiresResetError,
)


def _raw_graph_node(node_id: str, *, labels: list[str] | None = None, name: str | None = None) -> dict:
    return {
        "id": node_id,
        "labels": labels or ["MilvusKB", "Entity"],
        "properties": {"name": name or node_id, "kb_id": "kb_test"},
    }


def _raw_graph_edge(edge_id: str, source_id: str, target_id: str) -> dict:
    return {
        "id": edge_id,
        "type": "RELATED_TO",
        "source_id": source_id,
        "target_id": target_id,
        "properties": {},
    }


def test_normalize_extraction_result_defaults_and_validates_refs():
    result = normalize_extraction_result(
        {
            "entities": [{"text": "张三"}, {"text": "公司"}],
            "relations": [{"source": "张三", "target": "公司", "text": "任职于"}],
        },
        "llm",
    )

    assert result["entities"][0]["label"] == "Entity"
    assert result["relations"][0]["label"] == "RELATED_TO"
    assert result["relations"][0]["source"] == {"text": "张三", "label": "Entity", "attributes": []}
    assert result["relations"][0]["polarity"] == "positive"
    assert result["relations"][0]["assertion_kind"] == "fact"
    assert result["relations"][0]["evidence"] == {"quote": "", "start_char": None, "end_char": None}
    assert result["metadata"] == {"extractor_type": "llm", "schema_version": 1}


def test_normalize_extraction_result_preserves_v2_assertion_evidence():
    result = normalize_extraction_result(
        {
            "entities": [{"text": "产品"}, {"text": "离线模式"}],
            "relations": [
                {
                    "source": "产品",
                    "target": "离线模式",
                    "text": "不再支持离线模式",
                    "polarity": "negative",
                    "assertion_kind": "retraction",
                    "evidence": {"quote": "产品不再支持离线模式。", "start_char": 2, "end_char": 13},
                }
            ],
            "metadata": {"schema_version": 2, "provider": "test"},
        },
        "llm",
    )

    relation = result["relations"][0]
    assert relation["polarity"] == "negative"
    assert relation["assertion_kind"] == "retraction"
    assert relation["evidence"] == {"quote": "产品不再支持离线模式。", "start_char": 2, "end_char": 13}
    assert result["metadata"] == {"schema_version": 2, "provider": "test", "extractor_type": "llm"}


def test_llm_graph_extractor_enriches_evidence_offsets_and_v2_metadata():
    extractor = LLMGraphExtractor({"model_spec": "test/model"})
    source_text = "产品支持在线模式。产品不再支持离线模式。"

    result = extractor._enrich_evidence(
        {
            "relations": [
                {"evidence": {"quote": "产品不再支持离线模式。"}},
                {"evidence": {"quote": "原文不存在"}},
            ]
        },
        source_text,
    )

    start_char = source_text.index("产品不再支持离线模式。")
    assert result["metadata"]["schema_version"] == 2
    assert result["relations"][0]["evidence"] == {
        "quote": "产品不再支持离线模式。",
        "start_char": start_char,
        "end_char": start_char + len("产品不再支持离线模式。"),
    }
    assert result["relations"][1]["evidence"] == {
        "quote": "原文不存在",
        "start_char": None,
        "end_char": None,
    }


def test_build_graph_payload_only_publishes_positive_facts():
    normalized = normalize_extraction_result(
        {
            "entities": [{"text": "产品"}, {"text": "在线模式"}, {"text": "离线模式"}],
            "relations": [
                {"source": "产品", "target": "在线模式", "text": "支持", "label": "SUPPORTS"},
                {
                    "source": "产品",
                    "target": "离线模式",
                    "text": "不支持",
                    "label": "SUPPORTS",
                    "polarity": "negative",
                },
                {
                    "source": "产品",
                    "target": "在线模式",
                    "text": "撤回支持",
                    "label": "SUPPORTS",
                    "assertion_kind": "retraction",
                },
            ],
        },
        "llm",
    )

    payload = build_graph_payload(normalized)

    assert payload["entities"] == [
        {"id": "e1", "text": "产品", "label": "Entity", "attributes": []},
        {"id": "e2", "text": "在线模式", "label": "Entity", "attributes": []},
    ]
    assert payload["relations"] == [{"source": "e1", "target": "e2", "text": "支持", "label": "SUPPORTS"}]


def test_build_graph_payload_drops_entities_without_positive_facts():
    normalized = normalize_extraction_result(
        {
            "entities": [{"text": "产品"}, {"text": "离线模式"}, {"text": "孤立功能"}],
            "relations": [
                {
                    "source": "产品",
                    "target": "离线模式",
                    "text": "不再支持",
                    "label": "SUPPORTS",
                    "polarity": "negative",
                    "assertion_kind": "retraction",
                }
            ],
        },
        "llm",
    )

    payload = build_graph_payload(normalized)

    assert payload["entities"] == []
    assert payload["relations"] == []
    assert normalized["entities"] == [
        {"text": "产品", "label": "Entity", "attributes": []},
        {"text": "离线模式", "label": "Entity", "attributes": []},
        {"text": "孤立功能", "label": "Entity", "attributes": []},
    ]
    assert normalized["relations"][0]["evidence"] == {"quote": "", "start_char": None, "end_char": None}


def test_build_graph_payload_keeps_only_positive_relation_endpoints():
    normalized = normalize_extraction_result(
        {
            "entities": [
                {"text": "产品", "attributes": [{"text": "正式", "label": "status"}]},
                {"text": "在线模式"},
                {"text": "离线模式"},
            ],
            "relations": [
                {"source": "产品", "target": "在线模式", "text": "支持", "label": "SUPPORTS"},
                {
                    "source": "产品",
                    "target": "离线模式",
                    "text": "不支持",
                    "label": "SUPPORTS",
                    "polarity": "negative",
                },
            ],
        },
        "llm",
    )

    payload = build_graph_payload(normalized)

    assert [entity["text"] for entity in payload["entities"]] == ["产品", "在线模式"]
    assert payload["entities"][0]["attributes"] == [{"text": "正式", "label": "status"}]
    assert all(entity["text"] != "离线模式" for entity in payload["entities"])
    assert len(payload["relations"]) == 1


def test_normalize_extraction_result_accepts_llm_nested_relation_entities():
    result = normalize_extraction_result(
        {
            "relations": [
                {
                    "source": {
                        "text": "张三",
                        "label": "Person",
                        "attributes": [{"text": "工程师", "label": "Occupation"}],
                    },
                    "target": {"text": "公司", "label": "Organization"},
                    "text": "任职于",
                    "label": "WORKS_AT",
                }
            ]
        },
        "llm",
    )

    assert result["entities"] == [
        {"text": "张三", "label": "Person", "attributes": [{"text": "工程师", "label": "Occupation"}]},
        {"text": "公司", "label": "Organization", "attributes": []},
    ]
    assert result["relations"][0]["source"]["attributes"] == [{"text": "工程师", "label": "Occupation"}]
    assert result["relations"][0]["target"] == {"text": "公司", "label": "Organization", "attributes": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"entities": [{"text": "张三"}], "relations": [{"source": "张三", "target": "不存在", "text": "关系"}]},
        {"entities": [{"text": ""}], "relations": []},
    ],
)
def test_normalize_extraction_result_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        normalize_extraction_result(payload, "llm")


def test_llm_graph_extractor_rejects_custom_prompt():
    extractor = LLMGraphExtractor({"model_spec": "test/model", "prompt": "custom"})

    with pytest.raises(ValueError, match="不支持自定义完整 Prompt"):
        extractor.validate_options()


def test_llm_graph_extractor_rejects_unsafe_concurrency():
    extractor = LLMGraphExtractor({"model_spec": "test/model", "concurrency_count": MAX_CONCURRENCY_COUNT + 1})

    with pytest.raises(ValueError, match=f"1 到 {MAX_CONCURRENCY_COUNT}"):
        extractor.validate_options()


def test_llm_graph_extractor_defaults_to_safe_concurrency():
    extractor = LLMGraphExtractor({"model_spec": "test/model"})

    extractor.validate_options()

    assert extractor.options["concurrency_count"] == 5


@pytest.mark.asyncio
async def test_llm_graph_extractor_retries_transient_model_errors(monkeypatch):
    calls = 0
    model = SimpleNamespace()

    async def call(_messages, stream=False):
        nonlocal calls
        calls += 1
        if calls < MODEL_MAX_ATTEMPTS:
            try:
                raise TimeoutError("provider timeout")
            except TimeoutError as exc:
                raise RuntimeError("wrapped") from exc
        return SimpleNamespace(content='{"entities": [], "relations": []}')

    model.call = call
    select_model = MagicMock(return_value=model)
    sleep = AsyncMock()
    monkeypatch.setattr("yuxi.knowledge.graphs.extractors.llm.select_model", select_model)
    monkeypatch.setattr("yuxi.knowledge.graphs.extractors.llm.asyncio.sleep", sleep)
    extractor = LLMGraphExtractor({"model_spec": "test/model"})

    result = await extractor.extract("测试", chunk_metadata={"chunk_id": "chunk_1"})

    assert result["entities"] == []
    assert calls == MODEL_MAX_ATTEMPTS
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
    assert select_model.call_args.kwargs["timeout"] == MODEL_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_llm_graph_extractor_does_not_retry_non_transient_errors(monkeypatch):
    model = SimpleNamespace(call=AsyncMock(side_effect=ValueError("invalid request")))
    monkeypatch.setattr("yuxi.knowledge.graphs.extractors.llm.select_model", lambda **_kwargs: model)
    sleep = AsyncMock()
    monkeypatch.setattr("yuxi.knowledge.graphs.extractors.llm.asyncio.sleep", sleep)
    extractor = LLMGraphExtractor({"model_spec": "test/model"})

    with pytest.raises(ValueError, match="invalid request"):
        await extractor.extract("测试", chunk_metadata={"chunk_id": "chunk_1"})

    model.call.assert_awaited_once()
    sleep.assert_not_awaited()


def test_llm_graph_extractor_appends_schema_to_fixed_prompt():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "schema": "实体类型只能是 Person 或 Organization",
            "concurrency_count": 5,
            "model_params": {"temperature": 0.1},
        }
    )

    prompt = extractor._build_prompt("张三任职于公司")

    assert "请从下面文本中抽取实体和实体关系" in prompt
    assert "抽取 Schema 约束" in prompt
    assert "实体类型只能是 Person 或 Organization" in prompt
    assert "文本：\n张三任职于公司" in prompt


def test_llm_graph_extractor_uses_ontology_messages_and_metadata(monkeypatch):
    from yuxi.knowledge.graphs.ontology.registry import _build_ontology

    ontology = _build_ontology(
        {
            "registry_id": "test",
            "version": "1.0.0",
            "name": "Test",
            "status": "active",
            "entities": {"Product": {"description": "产品", "examples": []}},
            "relations": {},
        },
        entity_aliases={"Product": {"MCSTARS": ["MCX系统"]}},
        relation_aliases={},
        properties={},
        expected_registry_id="test",
    )
    entry = SimpleNamespace(
        registry_id="test",
        version="1.0.0",
        digest="test-digest",
        public_dict=lambda: {
            "registry_id": "test",
            "version": "1.0.0",
            "digest": "test-digest",
            "name": "Test",
            "status": "active",
            "source": "uploaded",
        },
    )
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.extractors.llm.resolve_ontology_registry",
        lambda _registry_id, _version, _digest: entry,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.extractors.llm.load_ontology",
        lambda _registry_id, _version, _digest: ontology,
    )
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "ontology_registry_id": "test",
            "ontology_version": "1.0.0",
        }
    )
    extractor.validate_options()

    messages = extractor._build_messages("MCX系统")
    result = extractor.normalize_result({"entities": [{"text": "MCX系统", "label": "Product"}], "relations": []})

    assert messages[0]["role"] == "system"
    assert "禁止创建列表之外" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "文本：\nMCX系统"}
    assert result["entities"][0]["text"] == "MCSTARS"
    assert result["metadata"]["schema_version"] == 1
    assert result["metadata"]["ontology_registry_id"] == "test"
    assert result["metadata"]["ontology_version"] == "1.0.0"
    assert result["metadata"]["ontology_digest"] == "test-digest"


def test_llm_graph_extractor_accepts_generic_builtin():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "ontology_registry_id": "tongyong",
            "ontology_version": "1.0.0",
        }
    )

    extractor.validate_options()

    assert extractor.ontology.registry_id == "tongyong"
    assert set(extractor.ontology.entities) == {"effect", "feature", "product", "technology"}


def test_graph_extractor_factory_supports_only_llm():
    assert GraphExtractorFactory.supported_types() == ["llm"]


def test_graph_extractor_factory_rejects_spacy():
    with pytest.raises(ValueError, match="spacy"):
        GraphExtractorFactory.create("spacy", {"model": "zh_core_web_sm"})


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_rejects_spacy():
    kb = SimpleNamespace(kb_type="milvus", additional_params={})

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            raise AssertionError("unsupported extractor should not be persisted")

    service = MilvusGraphService(kb_repo=Repo())

    with pytest.raises(ValueError, match="不支持的图谱抽取器类型"):
        await service.configure(
            "kb_test",
            extractor_type="spacy",
            extractor_options={"model": "zh_core_web_sm"},
            created_by="user_1",
        )


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_persists_updated_concurrency():
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 5},
            }
        },
    )

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            kb.additional_params = data["additional_params"]
            return kb

    chunk_repo = SimpleNamespace(
        count_current_by_kb_id=AsyncMock(return_value=0),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
        count_with_extraction_result_by_kb_id=AsyncMock(return_value=0),
    )
    graph_repo = SimpleNamespace(count_by_current_kb_id=AsyncMock(return_value=(3, 2)))
    service = MilvusGraphService(kb_repo=Repo(), chunk_repo=chunk_repo, graph_repo=graph_repo)

    await service.configure(
        "kb_test",
        extractor_type="llm",
        extractor_options={"model_spec": "test/model", "concurrency_count": 9},
        created_by="user_1",
    )
    status = await service.get_status("kb_test")

    assert status["config"]["extractor_options"]["concurrency_count"] == 9
    assert status["ontology"] == {"mode": "legacy"}
    assert status["entity_count"] == 3
    assert status["relationship_count"] == 2


@pytest.mark.asyncio
async def test_milvus_graph_service_status_returns_latest_task_details():
    kb = SimpleNamespace(kb_type="milvus", additional_params={})
    chunk_repo = SimpleNamespace(
        count_current_by_kb_id=AsyncMock(return_value=4),
        count_graph_pending_by_kb_id=AsyncMock(return_value=2),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=2),
        count_with_extraction_result_by_kb_id=AsyncMock(return_value=3),
    )
    graph_repo = SimpleNamespace(count_by_current_kb_id=AsyncMock(return_value=(3, 2)))
    task = SimpleNamespace(
        id="task-new",
        status="cancelled",
        progress=45.4,
        message="任务被取消",
        error=None,
        result={"success": 2, "failed": 0, "remaining": 2},
        cancel_requested=True,
        completed_at="2026-01-02T00:00:00Z",
    )
    task_repository = SimpleNamespace(find_latest_by_payload=AsyncMock(return_value=task))
    service = MilvusGraphService(
        kb_repo=SimpleNamespace(get_by_kb_id=AsyncMock(return_value=kb)),
        chunk_repo=chunk_repo,
        graph_repo=graph_repo,
    )

    status = await service.get_status("kb_test", task_repository=task_repository)

    task_repository.find_latest_by_payload.assert_awaited_once_with(
        task_type="knowledge_graph_index",
        payload_match={"kb_id": "kb_test"},
    )
    assert status["build_task_id"] == "task-new"
    assert status["build_task_status"] == "cancelled"
    assert status["build_task_progress"] == 45
    assert status["build_task_message"] == "任务被取消"
    assert status["build_task_result"] == {"success": 2, "failed": 0, "remaining": 2}
    assert status["build_task_cancel_requested"] is True


@pytest.mark.asyncio
async def test_milvus_graph_service_rejects_ontology_switch_when_cached_results_exist(monkeypatch):
    old_entry = SimpleNamespace(registry_id="old", version="1.0", digest="old-digest")
    new_entry = SimpleNamespace(registry_id="new", version="2.0", digest="new-digest")
    entries = {"old": old_entry, "new": new_entry}
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.ontology.resolve_ontology_registry",
        lambda registry_id, _version, _digest: entries[registry_id],
    )
    monkeypatch.setattr(GraphExtractorFactory, "create", lambda *_args, **_kwargs: MagicMock())
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": False,
                "extractor_type": "llm",
                "extractor_options": {
                    "model_spec": "test/model",
                    "ontology_registry_id": "old",
                    "ontology_version": "1.0",
                    "ontology_digest": "old-digest",
                },
            }
        },
    )
    kb_repo = SimpleNamespace(
        get_by_kb_id=AsyncMock(return_value=kb),
        update=AsyncMock(),
    )
    chunk_repo = SimpleNamespace(
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
        count_with_extraction_result_by_kb_id=AsyncMock(return_value=1),
    )
    graph_repo = SimpleNamespace(count_by_kb_id=AsyncMock(return_value=(0, 0)))
    service = MilvusGraphService(kb_repo=kb_repo, chunk_repo=chunk_repo, graph_repo=graph_repo)

    with pytest.raises(OntologySwitchRequiresResetError, match="请先重置"):
        await service.configure(
            "kb_test",
            extractor_type="llm",
            extractor_options={
                "model_spec": "test/model",
                "ontology_registry_id": "new",
                "ontology_version": "2.0",
                "ontology_digest": "new-digest",
            },
            created_by="user_1",
        )

    kb_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_milvus_graph_service_allows_ontology_switch_without_graph_data(monkeypatch):
    old_entry = SimpleNamespace(registry_id="old", version="1.0", digest="old-digest")
    new_entry = SimpleNamespace(registry_id="new", version="2.0", digest="new-digest")
    entries = {"old": old_entry, "new": new_entry}
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.ontology.resolve_ontology_registry",
        lambda registry_id, _version, _digest: entries[registry_id],
    )
    monkeypatch.setattr(GraphExtractorFactory, "create", lambda *_args, **_kwargs: MagicMock())
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": False,
                "extractor_type": "llm",
                "extractor_options": {
                    "model_spec": "test/model",
                    "ontology_registry_id": "old",
                    "ontology_version": "1.0",
                    "ontology_digest": "old-digest",
                },
            }
        },
    )
    kb_repo = SimpleNamespace(
        get_by_kb_id=AsyncMock(return_value=kb),
        update=AsyncMock(),
    )
    chunk_repo = SimpleNamespace(
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
        count_with_extraction_result_by_kb_id=AsyncMock(return_value=0),
    )
    graph_repo = SimpleNamespace(count_by_kb_id=AsyncMock(return_value=(0, 0)))
    service = MilvusGraphService(kb_repo=kb_repo, chunk_repo=chunk_repo, graph_repo=graph_repo)

    await service.configure(
        "kb_test",
        extractor_type="llm",
        extractor_options={
            "model_spec": "test/model",
            "ontology_registry_id": "new",
            "ontology_version": "2.0",
            "ontology_digest": "new-digest",
        },
        created_by="user_1",
    )

    kb_repo.update.assert_awaited_once()


def test_llm_graph_extractor_rejects_cached_result_from_different_ontology(monkeypatch):
    from yuxi.knowledge.graphs.ontology.registry import _build_ontology

    ontology = _build_ontology(
        {
            "registry_id": "test",
            "version": "1.0.0",
            "name": "Test",
            "status": "active",
            "entities": {"Product": {"description": "产品", "examples": []}},
            "relations": {},
        },
        entity_aliases={},
        relation_aliases={},
        properties={},
        expected_registry_id="test",
    )
    entry = SimpleNamespace(registry_id="test", version="1.0.0", digest="current")
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.extractors.llm.resolve_ontology_registry",
        lambda *_args: entry,
    )
    monkeypatch.setattr(
        "yuxi.knowledge.graphs.extractors.llm.load_ontology",
        lambda *_args: ontology,
    )
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "ontology_registry_id": "test",
            "ontology_version": "1.0.0",
            "ontology_digest": "current",
        }
    )
    extractor.validate_options()

    with pytest.raises(ValueError, match="不同的 Core Ontology"):
        extractor.normalize_result(
            {
                "entities": [{"text": "F10", "label": "Product"}],
                "relations": [],
                "metadata": {
                    "ontology_registry_id": "test",
                    "ontology_version": "1.0.0",
                    "ontology_digest": "old",
                },
            }
        )


@pytest.mark.asyncio
async def test_milvus_graph_service_reextracts_invalid_cached_result():
    cached = {"entities": "invalid", "relations": []}
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        chunk_index=0,
        content="产品支持组呼",
        extraction_result=cached,
    )
    extractor = SimpleNamespace(
        normalize_result=MagicMock(
            side_effect=[ValueError("缓存格式错误"), {"entities": [], "relations": [], "metadata": {}}]
        ),
        extract=AsyncMock(return_value={"entities": [], "relations": []}),
    )
    chunk_repo = SimpleNamespace(
        clear_extraction_result=AsyncMock(),
        update_extraction_result=AsyncMock(),
    )
    service = MilvusGraphService(chunk_repo=chunk_repo)

    result = await service._get_chunk_extraction_result("kb_test", chunk, extractor)

    assert result == {"entities": [], "relations": [], "metadata": {}}
    chunk_repo.clear_extraction_result.assert_awaited_once_with("chunk_1")
    extractor.extract.assert_awaited_once()
    assert extractor.extract.await_args.kwargs["chunk_metadata"] == {
        "kb_id": "kb_test",
        "chunk_id": "chunk_1",
        "file_id": "file_1",
        "chunk_index": 0,
    }
    chunk_repo.update_extraction_result.assert_awaited_once_with("chunk_1", result)
    assert extractor.normalize_result.call_count == 2


@pytest.mark.asyncio
async def test_milvus_graph_service_keeps_identity_mismatch_cache():
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        chunk_index=0,
        content="产品支持组呼",
        extraction_result={"metadata": {"ontology_digest": "old"}},
    )
    extractor = SimpleNamespace(
        normalize_result=MagicMock(side_effect=OntologyIdentityMismatchError("请先清空抽取结果后重试")),
        extract=AsyncMock(),
    )
    chunk_repo = SimpleNamespace(
        clear_extraction_result=AsyncMock(),
        update_extraction_result=AsyncMock(),
    )
    service = MilvusGraphService(chunk_repo=chunk_repo)

    with pytest.raises(OntologyIdentityMismatchError, match="请先清空"):
        await service._get_chunk_extraction_result("kb_test", chunk, extractor)

    chunk_repo.clear_extraction_result.assert_not_awaited()
    extractor.extract.assert_not_awaited()
    chunk_repo.update_extraction_result.assert_not_awaited()


def test_milvus_graph_service_writes_chunk_entity_and_relation():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    connection = SimpleNamespace(driver=driver)
    service = MilvusGraphService(neo4j_connection=connection)
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="张三任职于公司",
        start_char_pos=0,
        end_char_pos=8,
    )

    entities, triples = service.write_chunk_graph(
        "kb_test",
        chunk,
        normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {
                            "text": "张三",
                            "label": "Person",
                            "attributes": [{"text": "工程师", "label": "Occupation"}],
                        },
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ],
            },
            "llm",
        ),
    )

    assert [entity["name"] for entity in entities] == ["张三", "公司"]
    assert {entity["label"] for entity in entities} == {"Person", "Organization"}
    assert triples[0]["relation_type"] == "WORKS_AT"
    queries = [call.args[0] for call in tx.run.call_args_list]
    assert any("MERGE (c:Chunk:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (e:Entity:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (source)-[r:RELATION" in query for query in queries)
    entity_call = next(call for call in tx.run.call_args_list if "MERGE (e:Entity" in call.args[0])
    assert entity_call.kwargs["attributes"] == '[{"text": "工程师", "label": "Occupation"}]'


def test_milvus_graph_service_skips_empty_formal_projection():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="产品不再支持离线模式",
        start_char_pos=0,
        end_char_pos=12,
    )

    entities, triples = service.write_chunk_graph(
        "kb_test",
        chunk,
        normalize_extraction_result(
            {
                "entities": [{"text": "独立实体", "label": "Feature"}],
                "relations": [
                    {
                        "source": {"text": "产品", "label": "Product"},
                        "target": {"text": "离线模式", "label": "Feature"},
                        "text": "不再支持",
                        "label": "SUPPORTS",
                        "polarity": "negative",
                        "assertion_kind": "retraction",
                    }
                ],
            },
            "llm",
        ),
    )

    assert entities == []
    assert triples == []
    driver.session.assert_not_called()
    tx.run.assert_not_called()


def test_milvus_graph_service_delete_file_graph_uses_scoped_streaming_queries():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._delete_file_graph_from_neo4j("kb_test", "file_1")

    queries = [call.args[0] for call in tx.run.call_args_list]
    assert len(queries) == 3
    cleanup_query = queries[1]
    assert "file_id: $file_id" in cleanup_query
    assert "DELETE m" in cleanup_query
    assert "WITH DISTINCT e" in cleanup_query
    assert "collect(" not in cleanup_query
    assert "MATCH (e:Entity:MilvusKB:`kb_test` {kb_id: $kb_id})" not in cleanup_query
    assert "DETACH DELETE c" in queries[2]


def test_milvus_graph_service_process_query_result_keeps_complete_edges():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=2,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b"]


def test_milvus_graph_service_process_query_result_filters_edges_after_node_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=1,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_filters_edges_to_excluded_chunk_nodes():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("entity-a"),
                "t": _raw_graph_node("chunk-a", labels=["MilvusKB", "Chunk"]),
                "r": _raw_graph_edge("edge-entity-chunk", "entity-a", "chunk-a"),
            }
        ],
        limit=2,
        kb_id="kb_test",
        exclude_chunk=True,
    )

    assert [node["id"] for node in result["nodes"]] == ["entity-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_clamps_negative_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=-1,
        kb_id="kb_test",
    )

    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_query_nodes_empty_kb_id():
    service = MilvusGraphService()
    result = await service.query_nodes(kb_id=None, keyword="test")
    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_get_labels_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_labels(kb_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_milvus_graph_service_get_stats_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_stats(kb_id=None)
    assert result == {"total_nodes": 0, "total_edges": 0, "entity_types": []}
