from app.services.tools import KnowledgeTools


def test_selects_chinese_embedding_for_mostly_chinese_query():
    tools = KnowledgeTools()

    model = tools.select_embedding_model("如何关闭 SOS 报警")

    assert model == "BAAI/bge-large-zh-v1.5"


def test_selects_multilingual_embedding_when_english_ratio_exceeds_threshold():
    tools = KnowledgeTools()

    model = tools.select_embedding_model("How to configure P368 SOS alarm")

    assert model == "BAAI/m3e-base"
