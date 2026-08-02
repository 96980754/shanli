import pytest

from yuxi.repositories.knowledge_file_repository import (
    InvalidFolderNameError,
    KnowledgeFileRepository,
    normalize_folder_name,
    stable_advisory_lock_key,
)


def test_stable_advisory_lock_key_is_deterministic_and_namespaced():
    first = stable_advisory_lock_key("knowledge-file-content", "kb_1\0abc")
    assert first == stable_advisory_lock_key("knowledge-file-content", "kb_1\0abc")
    assert first != stable_advisory_lock_key("knowledge-file-name", "kb_1\0abc")
    assert -(2**63) <= first < 2**63


def test_keep_both_name_generation_is_case_insensitive_and_sequential():
    existing = {"report.pdf", "report (1).pdf"}
    assert KnowledgeFileRepository._next_available_filename("Report.PDF", existing) == "Report (2).PDF"


def test_processing_progress_is_clamped_to_protocol_range():
    assert KnowledgeFileRepository._sanitize_data({"processing_progress": -1})["processing_progress"] == 0
    assert KnowledgeFileRepository._sanitize_data({"processing_progress": 101})["processing_progress"] == 100


def test_folder_name_normalization_trims_and_applies_nfkc():
    assert normalize_folder_name("  Ｔｅｓｔ  ") == "Test"


@pytest.mark.parametrize("value", ["", "   ", ".", "..", "a/b", "a\\b", "bad\nname"])
def test_folder_name_normalization_rejects_empty_and_invalid_names(value):
    with pytest.raises(InvalidFolderNameError):
        normalize_folder_name(value)


def test_folder_name_normalization_preserves_case():
    assert normalize_folder_name("Test") != normalize_folder_name("test")
