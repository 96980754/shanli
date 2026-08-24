from yuxi.repositories.curated_qa_repository import hash_qa_question, normalize_qa_question


def test_normalize_qa_question_ignores_outer_and_repeated_whitespace():
    assert normalize_qa_question("  如何   重置密码？\n") == "如何 重置密码？"


def test_normalize_qa_question_casefolds_english():
    assert normalize_qa_question("How To RESET Password") == "how to reset password"


def test_hash_qa_question_is_stable_after_normalization():
    first = normalize_qa_question("  API   Key  ")
    second = normalize_qa_question("api key")
    assert first == second
    assert hash_qa_question(first) == hash_qa_question(second)
