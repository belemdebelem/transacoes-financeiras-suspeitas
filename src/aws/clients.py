"""Lazy factory for the boto3 clients used by the project.

boto3 is imported only when a client is actually requested, so the local
pipeline runs without the AWS dependencies installed.
"""

from __future__ import annotations

from typing import Any

from src.aws.settings import AwsSettings


def _boto3():
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "boto3 não está instalado. Use: pip install -r requirements.txt"
        ) from exc
    return boto3


def _client(service: str, settings: AwsSettings | None = None) -> Any:
    config = settings or AwsSettings.from_env()
    return _boto3().client(service, region_name=config.region)


def s3_client(settings: AwsSettings | None = None) -> Any:
    return _client("s3", settings)


def dynamodb_client(settings: AwsSettings | None = None) -> Any:
    return _client("dynamodb", settings)


def sns_client(settings: AwsSettings | None = None) -> Any:
    return _client("sns", settings)


def cloudwatch_client(settings: AwsSettings | None = None) -> Any:
    return _client("cloudwatch", settings)
