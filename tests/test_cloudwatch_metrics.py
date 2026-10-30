"""Tests for the custom CloudWatch metrics."""

from __future__ import annotations

import json

from src.aws.cloudwatch_metrics import (
    build_batch_metrics,
    publish_batch_metrics,
    publish_error_metric,
)
from src.aws.settings import DEFAULT_METRICS_NAMESPACE, AwsSettings
from src.processing.lambda_handler import process_file


class FakeCloudWatchClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


class FailingCloudWatchClient:
    def put_metric_data(self, **kwargs):
        raise RuntimeError("cloudwatch indisponível")


def _result(**overrides):
    result = {
        "records_read": 200,
        "records_processed": 200,
        "records_invalid": 0,
        "records_duplicated": 0,
        "alerts_generated": 18,
        "alerts_published": 18,
        "duration_ms": 1234.5,
        "dynamodb_written": 200,
        "processed_uploaded": True,
        "summary_uploaded": True,
    }
    result.update(overrides)
    return result


def _by_name(metrics):
    return {metric["MetricName"]: metric for metric in metrics}


def test_build_batch_metrics_contains_the_operational_values():
    settings = AwsSettings(metrics_enabled=True)

    metrics = _by_name(build_batch_metrics(_result(), settings))

    assert metrics["TransactionsRead"]["Value"] == 200
    assert metrics["TransactionsProcessed"]["Value"] == 200
    assert metrics["TransactionsInvalid"]["Value"] == 0
    assert metrics["TransactionsDuplicated"]["Value"] == 0
    assert metrics["AlertsGenerated"]["Value"] == 18
    assert metrics["AlertRatePercent"]["Value"] == 9.0
    assert metrics["AlertsPublished"]["Value"] == 18
    assert metrics["DynamoDBItemsWritten"]["Value"] == 200
    assert metrics["ProcessingDurationMs"]["Value"] == 1234.5
    assert metrics["ProcessingDurationMs"]["Unit"] == "Milliseconds"
    assert metrics["AlertRatePercent"]["Unit"] == "Percent"


def test_metrics_use_the_service_dimension():
    settings = AwsSettings(metrics_enabled=True, metrics_service="processor-test")

    for metric in build_batch_metrics(_result(), settings):
        assert metric["Dimensions"] == [{"Name": "Service", "Value": "processor-test"}]


def test_failure_metrics_are_added_when_integrations_are_configured():
    settings = AwsSettings(
        metrics_enabled=True,
        raw_bucket="bucket-test",
        dynamodb_table="table-test",
        sns_topic_arn="arn:aws:sns:us-east-2:123:topic-test",
        alerts_enabled=True,
    )
    result = _result(
        dynamodb_written=197,
        alerts_published=16,
        processed_uploaded=True,
        summary_uploaded=False,
    )

    metrics = _by_name(build_batch_metrics(result, settings))

    assert metrics["DynamoDBWriteFailures"]["Value"] == 3
    assert metrics["AlertsPublishFailures"]["Value"] == 2
    assert metrics["S3ProcessedUploadFailures"]["Value"] == 0
    assert metrics["S3SummaryUploadFailures"]["Value"] == 1


def test_failure_metrics_are_omitted_without_integrations():
    metrics = _by_name(build_batch_metrics(_result(), AwsSettings(metrics_enabled=True)))

    assert "DynamoDBWriteFailures" not in metrics
    assert "AlertsPublishFailures" not in metrics
    assert "S3ProcessedUploadFailures" not in metrics
    assert "S3SummaryUploadFailures" not in metrics


def test_alert_rate_is_zero_for_an_empty_batch():
    metrics = _by_name(
        build_batch_metrics(
            _result(records_processed=0, alerts_generated=0),
            AwsSettings(metrics_enabled=True),
        )
    )

    assert metrics["AlertRatePercent"]["Value"] == 0.0


def test_publish_batch_metrics_skips_when_disabled():
    client = FakeCloudWatchClient()

    published = publish_batch_metrics(
        _result(), settings=AwsSettings(metrics_enabled=False), client=client
    )

    assert published is False
    assert client.calls == []


def test_publish_batch_metrics_sends_the_namespace_and_metrics():
    client = FakeCloudWatchClient()
    settings = AwsSettings(metrics_enabled=True, metrics_namespace="CustomNamespace")

    published = publish_batch_metrics(_result(), settings=settings, client=client)

    assert published is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["Namespace"] == "CustomNamespace"
    assert len(call["MetricData"]) == 9


def test_publish_batch_metrics_does_not_break_the_pipeline_on_failure():
    published = publish_batch_metrics(
        _result(),
        settings=AwsSettings(metrics_enabled=True),
        client=FailingCloudWatchClient(),
    )

    assert published is False


def test_publish_error_metric_sends_a_batch_failure():
    client = FakeCloudWatchClient()

    published = publish_error_metric(
        "INVALID_JSON", settings=AwsSettings(metrics_enabled=True), client=client
    )

    assert published is True
    call = client.calls[0]
    assert call["Namespace"] == DEFAULT_METRICS_NAMESPACE
    assert call["MetricData"][0]["MetricName"] == "BatchFailures"
    assert call["MetricData"][0]["Value"] == 1


def test_publish_error_metric_skips_when_disabled():
    client = FakeCloudWatchClient()

    published = publish_error_metric(
        "INVALID_JSON", settings=AwsSettings(metrics_enabled=False), client=client
    )

    assert published is False
    assert client.calls == []


def test_metrics_settings_are_read_from_the_environment():
    settings = AwsSettings.from_env(
        {
            "METRICS_ENABLED": "true",
            "METRICS_NAMESPACE": "MinhaNamespace",
            "METRICS_SERVICE": "meu-servico",
        }
    )

    assert settings.metrics_enabled is True
    assert settings.metrics_namespace == "MinhaNamespace"
    assert settings.metrics_service == "meu-servico"


def test_blank_metrics_namespace_falls_back_to_the_default():
    settings = AwsSettings.from_env({"METRICS_NAMESPACE": "   "})

    assert settings.metrics_namespace == DEFAULT_METRICS_NAMESPACE


def test_process_file_publishes_metrics_when_enabled(tmp_path, monkeypatch):
    input_path = tmp_path / "raw.json"
    input_path.write_text(
        json.dumps(
            [
                {
                    "transaction_id": "txn_0001",
                    "timestamp": "2026-10-05T12:00:00Z",
                    "account_id": "ACC_0001",
                    "customer_id": "CUST_0001",
                    "transaction_type": "DEBIT",
                    "amount": 1500.0,
                    "currency": "BRL",
                }
            ]
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_publish(result, settings=None, client=None):
        captured["result"] = result
        captured["settings"] = settings
        return True

    monkeypatch.setattr(
        "src.processing.lambda_handler.cloudwatch_metrics.publish_batch_metrics",
        fake_publish,
    )
    monkeypatch.setenv("METRICS_ENABLED", "true")

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["metrics_published"] is True
    assert captured["result"]["records_processed"] == 1
    assert captured["settings"].metrics_enabled is True


def test_process_file_publishes_an_error_metric_when_it_fails(tmp_path, monkeypatch):
    captured = {}

    def fake_publish(error_code, settings=None, client=None):
        captured["error_code"] = error_code
        return True

    monkeypatch.setattr(
        "src.processing.lambda_handler.cloudwatch_metrics.publish_error_metric",
        fake_publish,
    )
    monkeypatch.setenv("METRICS_ENABLED", "true")

    result = process_file(tmp_path / "missing.json", tmp_path / "processed.json")

    assert result["status"] == "error"
    assert result["metrics_published"] is True
    assert captured["error_code"] == "FILE_NOT_FOUND"
