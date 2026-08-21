"""全局 embed_model 切换热生效单测。

修改设置页全局 embed_model 后，未显式指定模型的向量检索 / 图谱构建 / 展示应无需重启
即使用新模型。根因是元数据加载器此前在 load 时把 embedding spec resolve 一次并缓存进
databases_meta，之后全局默认切换不再生效；现改为存原始 spec、使用点实时
resolve_embedding_model（见 KnowledgeBase._load_metadata / get_database_info）。
"""

from __future__ import annotations

from yuxi.config.app import config, resolve_embedding_model
from yuxi.knowledge.base import KnowledgeBase
from yuxi.knowledge.implementations.milvus import MilvusKB


class _FakeKbRow:
    """替代 ORM 行，仅含 _load_metadata 用到的字段。"""

    def __init__(self, kb_id: str, spec: str | None) -> None:
        self.kb_id = kb_id
        self.name = f"库-{kb_id}"
        self.description = ""
        self.kb_type = "milvus"
        self.embedding_model_spec = spec
        self.llm_model_spec = None
        self.query_params = None
        self.additional_params = None
        self.created_at = None


def _make_minimal_kb() -> KnowledgeBase:
    """构造跳过 __init__（不触 DB/连接）的最小知识库实例。"""
    kb = object.__new__(MilvusKB)
    kb.work_dir = "/tmp/yuxi-test-kb"
    kb.databases_meta = {}
    kb.benchmarks_meta = {}
    kb._metadata_loaded = False
    return kb


def _seed_meta(kb: KnowledgeBase, kb_id: str, spec: str | None) -> None:
    kb.databases_meta[kb_id] = {
        "name": f"库-{kb_id}",
        "description": "",
        "kb_type": "milvus",
        "embedding_model_spec": spec,
        "llm_model_spec": None,
        "metadata": {"stats": {"file_count": 0, "chunk_count": 0, "token_count": 0}},
        "created_at": None,
        "query_params": {"options": {}},
    }


async def test_load_metadata_keeps_raw_spec(monkeypatch) -> None:
    """元数据加载器把原始 spec（可能为空）存入 databases_meta，不再提前 resolve。"""
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    rows = [_FakeKbRow("kb_follow", None), _FakeKbRow("kb_explicit", "spec-fixed")]

    async def fake_get_all(self):
        return rows

    monkeypatch.setattr(KnowledgeBaseRepository, "get_all", fake_get_all)

    kb = _make_minimal_kb()
    await kb._load_metadata()

    # 跟随全局默认的库存空值，显式指定的库存原值
    assert kb.databases_meta["kb_follow"]["embedding_model_spec"] is None
    assert kb.databases_meta["kb_explicit"]["embedding_model_spec"] == "spec-fixed"


async def test_get_database_info_reflects_global_change_without_reload(monkeypatch) -> None:
    """展示路径实时 resolve：修改全局默认后，无需重载元数据即返回新模型。"""
    kb = _make_minimal_kb()
    _seed_meta(kb, "kb_follow", None)
    _seed_meta(kb, "kb_explicit", "spec-fixed")
    kb._metadata_loaded = True

    monkeypatch.setattr(config, "embed_model", "global-model-a")
    assert kb.get_database_info("kb_follow")["embedding_model_spec"] == "global-model-a"

    # 修改全局默认：不重载元数据，新模型立即反映到展示
    monkeypatch.setattr(config, "embed_model", "global-model-b")
    assert kb.get_database_info("kb_follow")["embedding_model_spec"] == "global-model-b"

    # 显式指定 spec 的库不受全局默认影响
    assert kb.get_database_info("kb_explicit")["embedding_model_spec"] == "spec-fixed"


async def test_use_time_resolution_uses_current_global(monkeypatch) -> None:
    """使用点 resolve（milvus.py 各嵌入入口的同一表达式）跟随当前全局默认。"""
    monkeypatch.setattr(config, "embed_model", "global-model-c")
    # 模拟 milvus.py 中 `embedding_model_spec = resolve_embedding_model(databases_meta[kb_id].get(...))`
    raw_spec: str | None = None
    assert resolve_embedding_model(raw_spec) == "global-model-c"

    monkeypatch.setattr(config, "embed_model", "global-model-d")
    assert resolve_embedding_model(raw_spec) == "global-model-d"
