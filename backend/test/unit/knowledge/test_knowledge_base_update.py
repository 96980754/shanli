import asyncio
import types
from datetime import datetime

import pytest

from yuxi.knowledge.chunking.ragflow_like.nlp import count_tokens
from yuxi.knowledge.base import KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, slug: str, config: dict):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, slug: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def update_content(self, slug: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        return []

    async def aquery(self, query_text: str, slug: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, slug: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, slug: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, slug: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, slug: str, file_id: str) -> dict:
        return {}

    async def _save_metadata(self) -> None:
        pass


def make_kb(tmp_path):
    kb = FakeKnowledgeBase(str(tmp_path))
    kb.databases_meta = {
        "db": {
            "name": "Old name",
            "description": "Old description",
            "kb_type": "fake",
            "llm_model_spec": "provider:model-a",
        }
    }
    return kb


def make_file_record(file_id: str, meta: dict):
    return types.SimpleNamespace(
        file_id=file_id,
        kb_id=meta.get("kb_id"),
        parent_id=meta.get("parent_id"),
        filename=meta.get("filename", ""),
        file_type=meta.get("file_type"),
        path=meta.get("path"),
        minio_url=meta.get("minio_url"),
        markdown_file=meta.get("markdown_file"),
        status=meta.get("status"),
        content_hash=meta.get("content_hash"),
        file_size=meta.get("size", meta.get("file_size")),
        chunk_count=meta.get("chunk_count", 0),
        token_count=meta.get("token_count", 0),
        content_type=meta.get("content_type"),
        processing_params=meta.get("processing_params"),
        is_folder=meta.get("is_folder", False),
        error_message=meta.get("error"),
        created_by=meta.get("created_by"),
        updated_by=meta.get("updated_by"),
        created_at=None,
        updated_at=None,
        original_filename=meta.get("original_filename"),
    )


class FakeFileRepository:
    def __init__(self, records: dict[str, types.SimpleNamespace]):
        self.records = records
        self.update_calls = []

    async def list_by_kb_id(self, kb_id: str):
        return [record for record in self.records.values() if record.kb_id == kb_id]

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ):
        records = [
            record
            for record in self.records.values()
            if record.kb_id == kb_id
            and (not after_file_id or record.file_id > after_file_id)
            and (not files_only or not record.is_folder)
        ]
        records.sort(key=lambda record: record.file_id)
        return records[:limit]

    async def update_fields(self, *, file_id: str, data: dict, kb_id: str | None = None):
        record = self.records.get(file_id)
        if record is None or (kb_id and record.kb_id != kb_id):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self.update_calls.append((file_id, kb_id, dict(data)))
        return record

    async def get_kb_file_stats(self, kb_id: str):
        records = [record for record in self.records.values() if record.kb_id == kb_id]
        files = [record for record in records if not record.is_folder]
        return {
            "row_count": len(records),
            "file_count": len(files),
            "folder_count": len(records) - len(files),
            "total_size": sum(int(record.file_size or 0) for record in files),
            "chunk_count": sum(int(record.chunk_count or 0) for record in files),
            "token_count": sum(int(record.token_count or 0) for record in files),
            "pending_parse_count": sum(1 for record in files if record.status == "uploaded"),
            "pending_index_count": sum(1 for record in files if record.status in {"parsed", "error_indexing"}),
            "processing_count": sum(
                1 for record in files if record.status in {"processing", "waiting", "parsing", "indexing"}
            ),
        }


def make_file_records(files: dict[str, dict]) -> dict[str, types.SimpleNamespace]:
    return {file_id: make_file_record(file_id, meta) for file_id, meta in files.items()}


async def test_create_database_persists_allowed_record_fields(tmp_path, monkeypatch):
    created_payloads = []

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return None

        async def create(self, payload):
            created_payloads.append(payload)
            return types.SimpleNamespace(**payload)

        async def update(self, kb_id, data):
            raise AssertionError("create_database should insert new database metadata")

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )

    kb = FakeKnowledgeBase(str(tmp_path))
    share_config = {"access_level": "user", "department_ids": [], "user_uids": ["root"]}

    await kb.create_database(
        "New database",
        "New description",
        embedding_model_spec="provider:embedding",
        record_fields={
            "share_config": share_config,
            "created_by": "root",
            "category_id": 7,
            "unexpected_field": "ignored",
        },
        auto_generate_questions=False,
    )

    assert len(created_payloads) == 1
    payload = created_payloads[0]
    assert payload["share_config"] == share_config
    assert payload["created_by"] == "root"
    assert payload["category_id"] == 7
    assert "unexpected_field" not in payload
    assert "share_config" not in payload["additional_params"]
    assert "created_by" not in payload["additional_params"]


async def test_update_database_keeps_llm_spec_when_field_is_omitted(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description")
    await asyncio.sleep(0)

    assert result["llm_model_spec"] == "provider:model-a"
    assert kb.databases_meta["db"]["llm_model_spec"] == "provider:model-a"


async def test_update_database_clears_llm_spec_when_field_is_explicit(tmp_path):
    kb = make_kb(tmp_path)

    result = kb.update_database("db", "New name", "New description", None, update_llm_model_spec=True)
    await asyncio.sleep(0)

    assert result["llm_model_spec"] is None
    assert kb.databases_meta["db"]["llm_model_spec"] is None


async def test_update_database_keeps_embedding_spec_when_field_is_omitted(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["embedding_model_spec"] = "siliconflow-cn:BAAI/bge-m3"

    result = kb.update_database("db", "New name", "New description")
    await asyncio.sleep(0)

    assert result["embedding_model_spec"] == "siliconflow-cn:BAAI/bge-m3"
    assert kb.databases_meta["db"]["embedding_model_spec"] == "siliconflow-cn:BAAI/bge-m3"


async def test_update_database_switches_embedding_model_in_memory(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["embedding_model_spec"] = "siliconflow-cn:BAAI/bge-m3"

    result = kb.update_database(
        "db",
        "New name",
        "New description",
        embedding_model_spec="local:BAAI/bge-m3",
        update_embedding_model_spec=True,
    )
    await asyncio.sleep(0)

    assert result["embedding_model_spec"] == "local:BAAI/bge-m3"
    assert kb.databases_meta["db"]["embedding_model_spec"] == "local:BAAI/bge-m3"


def test_get_database_info_returns_persisted_content_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {
        "stats": {"row_count": 3, "file_count": 2, "chunk_count": 5, "token_count": 25}
    }

    result = kb.get_database_info("db")

    assert result["row_count"] == 3
    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 5
    assert result["stats"]["token_count"] == 25
    assert result["files"] == {}
    assert result["files_truncated"] is True


def test_get_database_info_prefers_metadata_stats(tmp_path):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {"stats": {"file_count": 2, "chunk_count": 8, "token_count": 40}}

    result = kb.get_database_info("db")

    assert result["stats"]["file_count"] == 2
    assert result["stats"]["chunk_count"] == 8
    assert result["stats"]["token_count"] == 40


async def test_refresh_database_stats_persists_metadata(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records(
        {
            "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 2, "token_count": 10},
            "folder-1": {
                "kb_id": "db",
                "filename": "folder",
                "is_folder": True,
                "chunk_count": 99,
                "token_count": 99,
            },
        }
    )
    file_repo = FakeFileRepository(records)
    persisted_kbs = []

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    stats = await kb.refresh_database_stats("db")

    assert stats["file_count"] == 1
    assert stats["chunk_count"] == 2
    assert stats["token_count"] == 10
    assert kb.databases_meta["db"]["metadata"]["stats"] == stats
    assert persisted_kbs == [("db", {"stats": stats})]


async def test_persist_kb_preserves_graph_config_added_outside_cache(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {"stats": {"file_count": 2}}
    updates = []

    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            assert kb_id == "db"
            return types.SimpleNamespace(
                additional_params={
                    "stats": {"file_count": 1},
                    "graph_build_config": {"locked": True, "extractor_type": "llm"},
                }
            )

        async def update(self, kb_id, data):
            updates.append((kb_id, data))

    monkeypatch.setattr(
        "yuxi.repositories.knowledge_base_repository.KnowledgeBaseRepository",
        FakeKnowledgeBaseRepository,
    )

    await kb._persist_kb("db")

    additional_params = updates[0][1]["additional_params"]
    assert additional_params["stats"] == {"file_count": 2}
    assert additional_params["graph_build_config"] == {"locked": True, "extractor_type": "llm"}
    assert kb.databases_meta["db"]["metadata"] == additional_params


async def test_repair_missing_file_stats_updates_files_and_database_metadata(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records(
        {
            "file-1": {"kb_id": "db", "filename": "alpha.md", "chunk_count": 0, "token_count": 0},
            "file-2": {"kb_id": "db", "filename": "beta.md", "chunk_count": 1, "token_count": 7},
            "folder-1": {
                "kb_id": "db",
                "filename": "folder",
                "is_folder": True,
                "chunk_count": 99,
                "token_count": 99,
            },
        }
    )
    file_repo = FakeFileRepository(records)
    persisted_kbs = []

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-1", "file-2"]
            return {"file-1": 2, "file-2": 3}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-1"]
            return [
                types.SimpleNamespace(file_id="file-1", content="alpha beta"),
                types.SimpleNamespace(file_id="file-1", content="中文"),
            ]

    async def persist_kb(kb_id):
        persisted_kbs.append((kb_id, dict(kb.databases_meta[kb_id]["metadata"])))

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta") + count_tokens("中文")
    expected_stats = {"file_count": 2, "chunk_count": 5, "token_count": expected_token_count + 7}
    assert records["file-1"].chunk_count == 2
    assert records["file-1"].token_count == expected_token_count
    assert records["file-2"].chunk_count == 3
    assert records["file-2"].token_count == 7
    for key, value in expected_stats.items():
        assert result["stats"][key] == value
    assert result["scanned_token_files"] == 1
    assert result["updated_chunk_files"] == 2
    assert result["updated_token_files"] == 1
    assert {file_id for file_id, _, _ in file_repo.update_calls} == {"file-1", "file-2"}
    persisted_stats = persisted_kbs[0][1]["stats"]
    for key, value in expected_stats.items():
        assert persisted_stats[key] == value


async def test_repair_missing_file_stats_skips_unindexed_files(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    kb.databases_meta["db"]["metadata"] = {}
    records = make_file_records(
        {
            "file-indexed": {
                "kb_id": "db",
                "filename": "alpha.md",
                "status": "indexed",
                "chunk_count": 0,
                "token_count": 0,
            },
            "file-uploaded": {
                "kb_id": "db",
                "filename": "beta.md",
                "status": "uploaded",
                "chunk_count": 9,
                "token_count": 90,
            },
            "file-parsed": {
                "kb_id": "db",
                "filename": "gamma.md",
                "status": "parsed",
                "chunk_count": 3,
                "token_count": 30,
            },
        }
    )
    file_repo = FakeFileRepository(records)

    class FakeChunkRepo:
        async def count_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return {"file-indexed": 2}

        async def list_by_file_ids(self, file_ids):
            assert file_ids == ["file-indexed"]
            return [types.SimpleNamespace(file_id="file-indexed", content="alpha beta")]

    async def persist_kb(kb_id):
        pass

    monkeypatch.setattr("yuxi.repositories.knowledge_chunk_repository.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr(
        "yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository",
        lambda: file_repo,
    )
    kb._persist_kb = persist_kb

    result = await kb.repair_missing_file_stats("db")

    expected_token_count = count_tokens("alpha beta")
    assert records["file-indexed"].chunk_count == 2
    assert records["file-indexed"].token_count == expected_token_count
    assert records["file-uploaded"].chunk_count == 0
    assert records["file-uploaded"].token_count == 0
    assert records["file-parsed"].chunk_count == 0
    assert records["file-parsed"].token_count == 0
    assert result["stats"]["file_count"] == 3
    assert result["stats"]["chunk_count"] == 2
    assert result["stats"]["token_count"] == expected_token_count
    assert result["scanned_files"] == 3
    assert result["scanned_indexed_files"] == 1
    assert result["skipped_unindexed_files"] == 2
    assert result["updated_files"] == 3
    assert {file_id for file_id, _, _ in file_repo.update_calls} == {
        "file-indexed",
        "file-uploaded",
        "file-parsed",
    }


def test_file_meta_to_record_data_coerces_iso_timestamps_to_datetime():
    # 回归：移动文件 read→persist 往返。_file_record_to_meta 把 DB 时间戳序列化为 UTC ISO 字符串，
    # _file_meta_to_record_data 若原样写回 TIMESTAMPTZ 列会触发 asyncpg DataError
    # （"invalid input for query argument ... expected a datetime"）。
    meta = {
        "file_id": "f1",
        "filename": "a.docx",
        "activated_at": "2026-08-18T20:45:33.092021Z",
        "superseded_at": None,
        "processing_task_updated_at": "2026-08-18T21:00:00.000000Z",
        "processing_task_lease_expires_at": "2026-08-18T22:00:00.000000Z",
    }
    data = KnowledgeBase._file_meta_to_record_data(meta)
    assert isinstance(data["activated_at"], datetime)
    assert isinstance(data["processing_task_updated_at"], datetime)
    assert isinstance(data["processing_task_lease_expires_at"], datetime)
    assert data["superseded_at"] is None


class MoveSpy(FakeKnowledgeBase):
    """move_file 单测探针：拦截读写，不触库。"""

    def __init__(self, files: dict[str, dict], tmp_path):
        super().__init__(str(tmp_path))
        self._files = files
        self.persisted: list[tuple[str, dict]] = []

    async def _load_file_meta(self, kb_id: str, file_id: str, *, refresh: bool = False) -> dict:
        return dict(self._files[file_id])

    async def _persist_file_meta(self, file_id: str, meta: dict) -> None:
        self._files[file_id] = dict(meta)
        self.persisted.append((file_id, dict(meta)))


def _file_meta(file_id: str, name: str, *, parent_id=None, is_folder=False) -> dict:
    return {"file_id": file_id, "kb_id": "kb", "filename": name, "parent_id": parent_id, "is_folder": is_folder}


async def test_move_file_into_virtual_folder_rewrites_filename(tmp_path):
    # 回归：最初 400 场景——目标是路径型虚拟目录（__virtual_folder__:root:xxx/），
    # 现改写 filename 路径前缀（poc资料/MNO/xxx.docx）而非写 parent_id
    spy = MoveSpy({"f1": _file_meta("f1", "POCSTARS定位产品介绍话术-海外版.docx")}, tmp_path)
    out = await spy.move_file("kb", "f1", "__virtual_folder__:root:poc资料/MNO/")
    assert out["filename"] == "poc资料/MNO/POCSTARS定位产品介绍话术-海外版.docx"
    assert out["parent_id"] is None
    assert out["normalized_name"] == "poc资料/mno/pocstars定位产品介绍话术-海外版.docx"
    assert spy.persisted[-1][0] == "f1"


async def test_move_file_out_of_virtual_path_back_to_root(tmp_path):
    spy = MoveSpy({"f1": _file_meta("f1", "poc资料/MNO/POCSTARS定位产品介绍话术-海外版.docx")}, tmp_path)
    out = await spy.move_file("kb", "f1", None)
    assert out["filename"] == "POCSTARS定位产品介绍话术-海外版.docx"
    assert out["parent_id"] is None


async def test_move_file_into_real_folder_strips_path_prefix(tmp_path):
    # 从虚拟路径目录移入真实文件夹：文件名收敛为 basename，parent_id 落真实文件夹
    spy = MoveSpy(
        {
            "f1": _file_meta("f1", "poc资料/MNO/POCSTARS定位产品介绍话术-海外版.docx"),
            "folder-real": _file_meta("folder-real", "销售话术", is_folder=True),
        },
        tmp_path,
    )
    out = await spy.move_file("kb", "f1", "folder-real")
    assert out["filename"] == "POCSTARS定位产品介绍话术-海外版.docx"
    assert out["parent_id"] == "folder-real"


async def test_move_file_into_virtual_folder_under_real_folder_sets_parent(tmp_path):
    # 虚拟目录 id 内嵌真实文件夹父上下文（__virtual_folder__:{folder}:{prefix}/）：parent_id 落库
    spy = MoveSpy(
        {"f1": _file_meta("f1", "a.docx"), "folder-real": _file_meta("folder-real", "资料", is_folder=True)},
        tmp_path,
    )
    out = await spy.move_file("kb", "f1", "__virtual_folder__:folder-real:sub/")
    assert out["filename"] == "sub/a.docx"
    assert out["parent_id"] == "folder-real"


async def test_move_file_rejects_virtual_target_for_folder_and_bad_id(tmp_path):
    spy = MoveSpy({"folder-a": _file_meta("folder-a", "a", is_folder=True)}, tmp_path)
    with pytest.raises(ValueError):
        await spy.move_file("kb", "folder-a", "__virtual_folder__:root:sub/")
    with pytest.raises(ValueError):
        await spy.move_file("kb", "folder-a", "__virtual_folder__:root:sub")  # 缺尾部斜杠
