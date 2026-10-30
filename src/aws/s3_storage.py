"""Helpers to read and write objects in the raw data bucket.

The bucket layout follows the project convention:

    raw-data/2026/10/05/transactions_20261005_224422.json
    processed/2026/10/05/transactions_20261005_224422_processed.json
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.aws.clients import s3_client
from src.aws.settings import AwsSettings


def _resolve(
    bucket: str | None,
    settings: AwsSettings | None,
    client: Any | None,
) -> tuple[str, Any]:
    config = settings or AwsSettings.from_env()
    target_bucket = bucket or config.raw_bucket
    if not target_bucket:
        raise ValueError(
            "Nenhum bucket configurado. Defina S3_RAW_BUCKET ou informe o bucket."
        )
    return target_bucket, client if client is not None else s3_client(config)


def build_key(prefix: str, filename: str, day: date | None = None) -> str:
    """Build a date partitioned key such as ``raw-data/2026/10/05/file.json``."""
    reference = day or datetime.now().date()
    return f"{prefix}{reference.year:04d}/{reference.month:02d}/{reference.day:02d}/{filename}"


def upload_file(
    local_path: str | Path,
    key: str,
    bucket: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> str:
    """Upload a local file and return the key it was stored under."""
    target_bucket, s3 = _resolve(bucket, settings, client)
    s3.put_object(
        Bucket=target_bucket,
        Key=key,
        Body=Path(local_path).read_bytes(),
        ContentType="application/json",
    )
    return key


def upload_json(
    payload: Any,
    key: str,
    bucket: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> str:
    """Upload a JSON serializable payload and return the key."""
    target_bucket, s3 = _resolve(bucket, settings, client)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=target_bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    return key


def download_bytes(
    bucket: str | None = None,
    key: str = "",
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> bytes:
    """Download the content of an object."""
    target_bucket, s3 = _resolve(bucket, settings, client)
    response = s3.get_object(Bucket=target_bucket, Key=key)
    return response["Body"].read()


def read_json(
    bucket: str | None = None,
    key: str = "",
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> Any:
    """Download an object and parse it as JSON."""
    return json.loads(download_bytes(bucket, key, settings, client))


def write_processed(
    payload: Any,
    key: str,
    bucket: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> str:
    """Store the processed batch as a JSON object in the bucket."""
    return upload_json(payload, key, bucket, settings, client)


def list_objects(
    bucket: str | None = None,
    prefix: str = "",
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """List the objects of a bucket, optionally filtered by prefix."""
    target_bucket, s3 = _resolve(bucket, settings, client)
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict[str, Any]] = []

    for page in paginator.paginate(Bucket=target_bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))

    return objects
