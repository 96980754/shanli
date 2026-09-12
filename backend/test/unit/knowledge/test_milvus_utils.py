from yuxi.knowledge.milvus_utils import escape_milvus_string_literal


def test_escape_milvus_string_literal():
    assert escape_milvus_string_literal("plain") == "plain"
    assert escape_milvus_string_literal('a"b') == 'a\\"b'
    assert escape_milvus_string_literal("a\\b") == "a\\\\b"
    assert escape_milvus_string_literal('a\\"b') == 'a\\\\\\"b'
