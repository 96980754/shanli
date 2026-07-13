from pathlib import Path

import pytest

from app.services.file_storage import LocalFileStorageService


def test_local_file_storage_keeps_failed_write_from_leaving_destination(tmp_path: Path, monkeypatch):
    storage = LocalFileStorageService(tmp_path)

    def fail_replace(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="disk full"):
        storage.save(b"content", "manual.txt", "text/plain", kb_id="1")

    assert not any(path.is_file() for path in tmp_path.rglob("*"))
