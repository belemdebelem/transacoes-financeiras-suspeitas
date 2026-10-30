"""Tests for the AWS integration layer."""

from __future__ import annotations

import io
import json
from datetime import date

import pytest

from src.aws import s3_storage
from src.aws.settings import AwsSettings


class FakeS3Client:
    """Minimal stand-in for the boto3 S3 client."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict]] = []

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {"ETag": "fake"}

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        body = self.objects.get(kwargs["Key"])
        if body is None:
            raise FileNotFoundError(kwargs["Key"])
        return {"Body": io.BytesIO(body)}

    def get_paginator(self, name):
        self.calls.append(("get_paginator", {"name": name}))

        class _Paginator:
            def paginate(self, **kwargs):
                prefix = kwargs.get("Prefix", "")
                contents = [
                    {"Key": key, "Size": len(body)}
                    for key, body in self_client.objects.items()
                    if key.startswith(prefix)
                ]
                return [{"Contents": contents}]

        self_client = self
        return _Paginator()


@pytest.fixture
def client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def settings() -> AwsSettings:
    return AwsSettings(
        region="us-east-2",
        raw_bucket="bucket-test",
        raw_prefix="raw-data/",
        processed_prefix="processed/",
    )


class TestSettings:
    def test_defaults_without_environment(self):
        config = AwsSettings.from_env({})

        assert config.region == "us-east-2"
        assert config.raw_bucket is None
        assert config.s3_configured is False
        assert config.dynamodb_configured is False
        assert config.sns_configured is False
        assert config.is_aws_configured is False

    def test_reads_environment(self):
        config = AwsSettings.from_env(
            {
                "AWS_REGION": "sa-east-1",
                "S3_RAW_BUCKET": "meu-bucket",
                "DYNAMODB_TABLE": "minha-tabela",
                "SNS_TOPIC_ARN": "arn:aws:sns:sa-east-1:123:alertas",
                "ALERTS_ENABLED": "true",
                "METRICS_ENABLED": "yes",
            }
        )

        assert config.region == "sa-east-1"
        assert config.raw_bucket == "meu-bucket"
        assert config.dynamodb_configured is True
        assert config.sns_configured is True
        assert config.alerts_enabled is True
        assert config.metrics_enabled is True
        assert config.is_aws_configured is True

    def test_blank_values_are_treated_as_missing(self):
        config = AwsSettings.from_env({"S3_RAW_BUCKET": "   "})

        assert config.raw_bucket is None
        assert config.s3_configured is False

    def test_default_region_falls_back_to_aws_default_region(self):
        config = AwsSettings.from_env({"AWS_DEFAULT_REGION": "eu-west-1"})

        assert config.region == "eu-west-1"

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_flags(self, value):
        assert AwsSettings.from_env({"ALERTS_ENABLED": value}).alerts_enabled is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "qualquer"])
    def test_falsy_flags(self, value):
        assert AwsSettings.from_env({"ALERTS_ENABLED": value}).alerts_enabled is False


class TestS3Storage:
    def test_build_key_uses_date_partition(self):
        key = s3_storage.build_key("raw-data/", "lote.json", date(2026, 10, 5))

        assert key == "raw-data/2026/10/05/lote.json"

    def test_upload_file_stores_the_object(self, tmp_path, settings, client):
        source = tmp_path / "lote.json"
        source.write_text('{"transactions": []}', encoding="utf-8")

        key = s3_storage.upload_file(source, "raw-data/2026/10/05/lote.json", settings=settings, client=client)

        assert key == "raw-data/2026/10/05/lote.json"
        assert client.objects[key] == b'{"transactions": []}'

    def test_upload_json_serializes_the_payload(self, settings, client):
        key = s3_storage.upload_json(
            [{"transaction_id": "t1"}], "processed/lote.json", settings=settings, client=client
        )

        assert json.loads(client.objects[key]) == [{"transaction_id": "t1"}]

    def test_download_bytes_returns_the_content(self, settings, client):
        s3_storage.upload_json({"ok": True}, "raw-data/a.json", settings=settings, client=client)

        assert s3_storage.download_bytes(key="raw-data/a.json", settings=settings, client=client) == b'{\n  "ok": true\n}'

    def test_read_json_parses_the_object(self, settings, client):
        s3_storage.upload_json({"ok": True}, "raw-data/a.json", settings=settings, client=client)

        assert s3_storage.read_json(key="raw-data/a.json", settings=settings, client=client) == {"ok": True}

    def test_list_objects_filters_by_prefix(self, settings, client):
        s3_storage.upload_json({"a": 1}, "raw-data/a.json", settings=settings, client=client)
        s3_storage.upload_json({"b": 2}, "processed/b.json", settings=settings, client=client)

        raw = s3_storage.list_objects(prefix="raw-data/", settings=settings, client=client)
        everything = s3_storage.list_objects(settings=settings, client=client)

        assert [obj["Key"] for obj in raw] == ["raw-data/a.json"]
        assert len(everything) == 2

    def test_missing_bucket_raises_a_clear_error(self, client):
        with pytest.raises(ValueError, match="S3_RAW_BUCKET"):
            s3_storage.upload_json({"a": 1}, "raw-data/a.json", settings=AwsSettings(), client=client)

    def test_write_processed_uses_the_given_key(self, settings, client):
        key = s3_storage.write_processed(
            [{"transaction_id": "t1"}],
            "processed/2026/10/05/lote_processed.json",
            settings=settings,
            client=client,
        )

        assert key in client.objects
        assert json.loads(client.objects[key])[0]["transaction_id"] == "t1"
