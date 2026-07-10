from app.services.kb_service import InMemoryKnowledgeBaseService


def test_kb_service_creates_lists_and_gets_knowledge_base():
    service = InMemoryKnowledgeBaseService()

    created = service.create(name="产品知识库", description="产品资料", visibility="department")

    assert created["name"] == "产品知识库"
    assert created["doc_count"] == 0
    assert service.get(created["id"])["id"] == created["id"]
    assert service.list()[0]["id"] == created["id"]


def test_kb_service_returns_none_for_missing_knowledge_base():
    service = InMemoryKnowledgeBaseService()

    assert service.get("missing") is None
