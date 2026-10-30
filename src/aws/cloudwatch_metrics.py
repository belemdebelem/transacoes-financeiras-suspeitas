"""Publish custom CloudWatch metrics for the processing pipeline.

Metrics are optional and best effort: when ``METRICS_ENABLED`` is false, or
when CloudWatch is unavailable, the pipeline continues normally.
"""

from __future__ import annotations

import logging
from typing import Any

from src.aws.clients import cloudwatch_client
from src.aws.settings import AwsSettings

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "SuspiciousTransactions"
DEFAULT_SERVICE_NAME = "transaction-processor"

UNIT_COUNT = "Count"
UNIT_MILLISECONDS = "Milliseconds"
UNIT_PERCENT = "Percent"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dimensions(settings: AwsSettings) -> list[dict[str, str]]:
    return [{"Name": "Service", "Value": settings.metrics_service}]


def _metric(
    name: str,
    value: float,
    unit: str,
    settings: AwsSettings,
) -> dict[str, Any]:
    return {
        "MetricName": name,
        "Value": float(value),
        "Unit": unit,
        "Dimensions": _dimensions(settings),
    }


def build_batch_metrics(
    result: dict[str, Any],
    settings: AwsSettings | None = None,
) -> list[dict[str, Any]]:
    """Build the CloudWatch metric data for one processed batch."""
    config = settings or AwsSettings.from_env()
    processed = _as_int(result.get("records_processed"))
    alerts = _as_int(result.get("alerts_generated"))
    alert_rate = round(alerts / processed * 100, 2) if processed else 0.0

    metrics = [
        _metric("TransactionsRead", _as_int(result.get("records_read")), UNIT_COUNT, config),
        _metric("TransactionsProcessed", processed, UNIT_COUNT, config),
        _metric("TransactionsInvalid", _as_int(result.get("records_invalid")), UNIT_COUNT, config),
        _metric(
            "TransactionsDuplicated",
            _as_int(result.get("records_duplicated")),
            UNIT_COUNT,
            config,
        ),
        _metric("AlertsGenerated", alerts, UNIT_COUNT, config),
        _metric("AlertRatePercent", alert_rate, UNIT_PERCENT, config),
        _metric(
            "ProcessingDurationMs",
            _as_float(result.get("duration_ms")),
            UNIT_MILLISECONDS,
            config,
        ),
        _metric("AlertsPublished", _as_int(result.get("alerts_published")), UNIT_COUNT, config),
        _metric(
            "DynamoDBItemsWritten",
            _as_int(result.get("dynamodb_written")),
            UNIT_COUNT,
            config,
        ),
    ]

    if config.dynamodb_configured:
        metrics.append(
            _metric(
                "DynamoDBWriteFailures",
                max(0, processed - _as_int(result.get("dynamodb_written"))),
                UNIT_COUNT,
                config,
            )
        )
    if config.sns_configured and config.alerts_enabled:
        metrics.append(
            _metric(
                "AlertsPublishFailures",
                max(0, alerts - _as_int(result.get("alerts_published"))),
                UNIT_COUNT,
                config,
            )
        )
    if config.s3_configured:
        metrics.append(
            _metric(
                "S3ProcessedUploadFailures",
                0 if result.get("processed_uploaded") else 1,
                UNIT_COUNT,
                config,
            )
        )
        metrics.append(
            _metric(
                "S3SummaryUploadFailures",
                0 if result.get("summary_uploaded") else 1,
                UNIT_COUNT,
                config,
            )
        )

    return metrics


def publish_batch_metrics(
    result: dict[str, Any],
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> bool:
    """Publish the metrics of a processed batch to CloudWatch."""
    config = settings or AwsSettings.from_env()
    if not config.metrics_enabled:
        logger.debug("CloudWatch não configurado (METRICS_ENABLED=false). Pulando métricas.")
        return False

    try:
        cw = client if client is not None else cloudwatch_client(config)
        cw.put_metric_data(
            Namespace=config.metrics_namespace,
            MetricData=build_batch_metrics(result, config),
        )
        return True
    except Exception as exc:
        logger.warning("Falha ao publicar métricas no CloudWatch: %s", exc)
        return False


def publish_error_metric(
    error_code: str,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> bool:
    """Publish one failure metric for a batch that could not be processed."""
    config = settings or AwsSettings.from_env()
    if not config.metrics_enabled:
        return False

    try:
        cw = client if client is not None else cloudwatch_client(config)
        cw.put_metric_data(
            Namespace=config.metrics_namespace,
            MetricData=[
                _metric("BatchFailures", 1, UNIT_COUNT, config),
            ],
        )
        logger.info("Métrica de falha publicada no CloudWatch.", extra={"error_code": error_code})
        return True
    except Exception as exc:
        logger.warning("Falha ao publicar métrica de erro no CloudWatch: %s", exc)
        return False
