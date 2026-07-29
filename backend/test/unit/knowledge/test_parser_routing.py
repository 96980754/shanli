from __future__ import annotations

from yuxi.knowledge.parser.unified import SUPPORTED_FILE_EXTENSIONS, is_supported_file_extension


def test_stable_upload_format_list_includes_images_and_legacy_office_contract() -> None:
    assert SUPPORTED_FILE_EXTENSIONS == (
        ".txt",
        ".md",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".doc",
        ".xls",
        ".ppt",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    )


def test_video_remains_outside_the_supported_upload_contract() -> None:
    for filename in ("video.mp4", "audio.mp3"):
        assert not is_supported_file_extension(filename)
