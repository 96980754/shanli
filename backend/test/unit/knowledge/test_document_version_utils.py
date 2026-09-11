from yuxi.knowledge.utils.document_version import parse_filename_version, same_version, version_key


def test_parse_explicit_filename_versions() -> None:
    assert parse_filename_version("操作手册-V1.10.docx") == ("操作手册", "1.10")
    assert parse_filename_version("操作手册-1.2.3.pdf") == ("操作手册", "1.2.3")
    assert parse_filename_version("操作手册 版本2.wps") == ("操作手册", "2")


def test_parse_rejects_ambiguous_numeric_suffixes() -> None:
    assert parse_filename_version("report-2024.pdf") is None
    assert parse_filename_version("测试1.docx") is None
    assert parse_filename_version("操作手册-最终版.docx") is None


def test_version_key_compares_segments_without_float_loss() -> None:
    assert version_key("1.10") > version_key("1.1")
    assert version_key("3.0") > version_key("2.8")
    assert version_key("1.2.3") == (1, 2, 3)


def test_same_version_normalizes_prefix_and_leading_zeroes() -> None:
    assert same_version("V01.02", "1.2")
    assert not same_version("1.10", "1.1")
    assert not same_version("1.2-beta", "1.2")
