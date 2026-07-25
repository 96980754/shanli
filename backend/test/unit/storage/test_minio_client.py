from types import SimpleNamespace

import pytest
from minio.error import S3Error

from yuxi.storage.minio.client import MinIOClient, StorageError


def test_ensure_bucket_exists_tolerates_concurrent_creation():
    class ConcurrentBucketClient:
        def bucket_exists(self, *, bucket_name: str) -> bool:
            assert bucket_name == "knowledgebases"
            return False

        def make_bucket(self, *, bucket_name: str) -> None:
            raise S3Error(
                SimpleNamespace(),
                "BucketAlreadyOwnedByYou",
                "bucket was created by the concurrent request",
                f"/{bucket_name}",
                "request-id",
                "host-id",
                bucket_name,
            )

    client = MinIOClient()
    client._client = ConcurrentBucketClient()

    assert client.ensure_bucket_exists("knowledgebases") is True


def test_ensure_bucket_exists_does_not_expose_s3_request_details():
    class FailingBucketClient:
        def bucket_exists(self, *, bucket_name: str) -> bool:
            return False

        def make_bucket(self, *, bucket_name: str) -> None:
            raise S3Error(
                SimpleNamespace(),
                "AccessDenied",
                "private storage detail",
                f"/{bucket_name}/private-object-key",
                "private-request-id",
                "private-host-id",
                bucket_name,
            )

    client = MinIOClient()
    client._client = FailingBucketClient()

    with pytest.raises(StorageError) as exc_info:
        client.ensure_bucket_exists("knowledgebases")

    error = str(exc_info.value)
    assert "private" not in error
    assert "request-id" not in error
    assert "object-key" not in error
