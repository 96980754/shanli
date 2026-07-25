from __future__ import annotations

from yuxi.knowledge.parser.unified import SUPPORTED_FILE_EXTENSIONS, is_supported_file_extension


def test_stable_upload_format_list_contains_only_pr2_first_batch() -> None:
    assert SUPPORTED_FILE_EXTENSIONS == (".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx")


def test_legacy_office_images_and_video_are_not_advertised_as_supported() -> None:
    for filename in (
        "legacy.doc",
        "legacy.xls",
        "legacy.ppt",
        "image.png",
        "animated.gif",
        "image.webp",
        "video.mp4",
    ):
        assert not is_supported_file_extension(filename)
