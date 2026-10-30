"""Configuration of the AWS resources used by the project.

Every value comes from environment variables, so the same code runs locally
and inside the Lambda without changes. When a variable is absent, the related
AWS integration is simply disabled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_REGION = "us-east-2"
DEFAULT_RAW_PREFIX = "raw-data/"
DEFAULT_PROCESSED_PREFIX = "processed/"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_METRICS_NAMESPACE = "SuspiciousTransactions"
DEFAULT_METRICS_SERVICE = "transaction-processor"

TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def _as_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass(frozen=True)
class AwsSettings:
    """Resolved configuration of the AWS integration."""

    region: str = DEFAULT_REGION
    raw_bucket: str | None = None
    raw_prefix: str = DEFAULT_RAW_PREFIX
    processed_prefix: str = DEFAULT_PROCESSED_PREFIX
    dynamodb_table: str | None = None
    sns_topic_arn: str | None = None
    alerts_enabled: bool = False
    metrics_enabled: bool = False
    metrics_namespace: str = DEFAULT_METRICS_NAMESPACE
    metrics_service: str = DEFAULT_METRICS_SERVICE
    log_level: str = DEFAULT_LOG_LEVEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AwsSettings":
        source = env if env is not None else os.environ
        region = (
            source.get("AWS_REGION")
            or source.get("AWS_DEFAULT_REGION")
            or DEFAULT_REGION
        )
        return cls(
            region=region,
            raw_bucket=_as_optional(source.get("S3_RAW_BUCKET")),
            raw_prefix=source.get("S3_RAW_PREFIX", DEFAULT_RAW_PREFIX),
            processed_prefix=source.get(
                "S3_PROCESSED_PREFIX", DEFAULT_PROCESSED_PREFIX
            ),
            dynamodb_table=_as_optional(source.get("DYNAMODB_TABLE")),
            sns_topic_arn=_as_optional(source.get("SNS_TOPIC_ARN")),
            alerts_enabled=_as_bool(source.get("ALERTS_ENABLED")),
            metrics_enabled=_as_bool(source.get("METRICS_ENABLED")),
            metrics_namespace=(
                _as_optional(source.get("METRICS_NAMESPACE"))
                or DEFAULT_METRICS_NAMESPACE
            ),
            metrics_service=(
                _as_optional(source.get("METRICS_SERVICE")) or DEFAULT_METRICS_SERVICE
            ),
            log_level=source.get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        )

    @property
    def s3_configured(self) -> bool:
        """Whether a raw data bucket was configured."""
        return bool(self.raw_bucket)

    @property
    def dynamodb_configured(self) -> bool:
        """Whether a destination table was configured."""
        return bool(self.dynamodb_table)

    @property
    def sns_configured(self) -> bool:
        """Whether an alert topic was configured."""
        return bool(self.sns_topic_arn)

    @property
    def is_aws_configured(self) -> bool:
        """Whether the full pipeline can run in the cloud."""
        return self.s3_configured and self.dynamodb_configured and self.sns_configured
