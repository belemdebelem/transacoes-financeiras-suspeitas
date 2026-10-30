"""Batch processing entrypoint, usable locally and as an AWS Lambda handler."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from src.aws import cloudwatch_metrics
from src.aws import s3_storage
from src.aws import dynamodb as dynamodb_storage
from src.aws import sns_publisher
from src.aws.settings import AwsSettings
from src.monitoring.operational_metrics import export_summary
from src.processing.validation import validate_batch
from src.rules.risk_rules import apply_risk_rules
from src.rules.rules_config import DEFAULT_CONFIG, RuleConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
S3_RAW_PREFIX = "raw-data/"
MIRROR_ENV_VAR = "LOCAL_S3_MIRROR"


def _log(level: int, event: str, **fields: Any) -> None:
    """Emit a single structured log line, ready to be read in CloudWatch."""
    payload = {"event": event, **fields}
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))


def _resolve_input_path(input_path: str | None = None) -> Path:
    if input_path:
        return Path(input_path)

    latest_file = max(RAW_DIR.glob("*.json"), default=None)
    if latest_file is None:
        raise FileNotFoundError("Nenhum arquivo de transações foi encontrado em data/raw.")
    return latest_file


def _resolve_output_path(
    input_path: str | Path | None, output_path: str | Path | None = None
) -> Path:
    if output_path:
        return Path(output_path)

    source = Path(input_path) if input_path else _resolve_input_path()
    return source.parent.parent / "processed" / f"{source.stem}_processed.json"


def _resolve_s3_local_path(bucket: str, key: str) -> Path:
    """Map an S3 object key to the local mirror of the raw data folder.

    The local mirror keeps a flat structure, so the date folders of the bucket
    layout (``raw-data/2026/10/05/file.json``) are reduced to the file name.
    """
    decoded = unquote(str(key)).lstrip("/")
    relative = (
        decoded[len(S3_RAW_PREFIX) :] if decoded.startswith(S3_RAW_PREFIX) else decoded
    )
    mirror = Path(os.environ.get(MIRROR_ENV_VAR, RAW_DIR))
    return mirror / PurePosixPath(relative).name


def _resolve_tmp_output_path(input_path: str | Path) -> Path:
    source = Path(input_path)
    return Path(tempfile.gettempdir()) / "processed" / f"{source.stem}_processed.json"


def load_transactions(file_path: str | Path) -> list[dict[str, Any]]:
    with Path(file_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        for key in ("transactions", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        raise ValueError(
            "Arquivo JSON não continha uma lista de transações nem um campo 'transactions'."
        )

    if isinstance(payload, list):
        return payload

    raise ValueError("Formato inválido para o arquivo de transação.")


def _deduplicate(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep the first occurrence of every transaction id."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    duplicates: list[str] = []

    for record in records:
        transaction_id = str(record.get("transaction_id") or "")
        if transaction_id in seen:
            duplicates.append(transaction_id)
            continue
        seen.add(transaction_id)
        unique.append(record)

    return unique, duplicates


def _error_result(
    error_code: str,
    error: Exception,
    input_path: str | Path | None,
    started_at: float,
    publish_metrics: bool = True,
) -> dict[str, Any]:
    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    result = {
        "status": "error",
        "error_code": error_code,
        "message": str(error),
        "input_path": str(input_path) if input_path else None,
        "duration_ms": duration_ms,
    }
    result["metrics_published"] = (
        cloudwatch_metrics.publish_error_metric(error_code) if publish_metrics else False
    )
    _log(logging.ERROR, "batch_failed", **result)
    return result


def process_transaction_batch(
    transactions: list[dict[str, Any]],
    config: RuleConfig = DEFAULT_CONFIG,
) -> list[dict[str, Any]]:
    return apply_risk_rules(transactions, config)


def process_file(
    input_path: str | Path | None,
    output_path: str | Path | None = None,
    config: RuleConfig = DEFAULT_CONFIG,
    integrations_enabled: bool = True,
) -> dict[str, Any]:
    """Validate and classify a batch, optionally running configured AWS integrations."""
    started_at = time.perf_counter()

    try:
        source = Path(input_path) if input_path else _resolve_input_path()
    except (OSError, ValueError) as exc:
        return _error_result("FILE_NOT_FOUND", exc, input_path, started_at, integrations_enabled)

    target = Path(output_path) if output_path else _resolve_output_path(source)
    summary_target = target.with_name(f"{target.stem}_summary.json")

    try:
        raw_records = load_transactions(source)
    except FileNotFoundError as exc:
        return _error_result("FILE_NOT_FOUND", exc, source, started_at, integrations_enabled)
    except json.JSONDecodeError as exc:
        return _error_result("INVALID_JSON", exc, source, started_at, integrations_enabled)
    except (OSError, ValueError) as exc:
        return _error_result("INVALID_PAYLOAD", exc, source, started_at, integrations_enabled)

    validation = validate_batch(raw_records)
    unique_records, duplicates = _deduplicate(validation.valid)
    processed = process_transaction_batch(unique_records, config)
    alerts = [transaction for transaction in processed if transaction.get("alert")]

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(processed, handle, ensure_ascii=False, indent=2)

    export_summary(processed, summary_target, invalid_records=len(validation.invalid))

    processing_date = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    dynamodb_written = 0
    alerts_published = 0
    processed_uploaded = False
    summary_uploaded = False
    settings: AwsSettings | None = None
    if integrations_enabled:
        try:
            settings = AwsSettings.from_env()
            if settings.dynamodb_configured:
                dynamodb_written = dynamodb_storage.put_processed_transactions(
                    processed, settings=settings, processed_at=processing_date
                )
                _log(logging.INFO, "dynamodb_written", count=dynamodb_written, table=settings.dynamodb_table)
            if settings.sns_configured and settings.alerts_enabled:
                alerts_published = sns_publisher.publish_alerts(processed, settings=settings)
                _log(logging.INFO, "sns_alerts_published", count=alerts_published, topic_arn=settings.sns_topic_arn)
            elif settings.sns_configured and not settings.alerts_enabled:
                _log(logging.INFO, "sns_skipped_alerts_disabled", topic_arn=settings.sns_topic_arn)
            if settings.s3_configured:
                try:
                    processed_key = s3_storage.build_key(settings.processed_prefix, target.name)
                    s3_storage.write_processed(processed, processed_key, bucket=settings.raw_bucket, settings=settings)
                    processed_uploaded = True
                    _log(logging.INFO, "s3_processed_uploaded", bucket=settings.raw_bucket, key=processed_key)
                except Exception as exc:
                    _log(logging.WARNING, "s3_processed_upload_failed", error=str(exc))
                try:
                    summary_key = s3_storage.build_key(settings.processed_prefix, summary_target.name)
                    summary_payload = json.loads(summary_target.read_text(encoding="utf-8"))
                    s3_storage.upload_json(summary_payload, summary_key, bucket=settings.raw_bucket, settings=settings)
                    summary_uploaded = True
                    _log(logging.INFO, "s3_summary_uploaded", bucket=settings.raw_bucket, key=summary_key)
                except Exception as exc:
                    _log(logging.WARNING, "s3_summary_upload_failed", error=str(exc))
        except Exception as exc:
            _log(logging.WARNING, "aws_integration_failed", error=str(exc))

    result = {
        "status": "success",
        "input_path": str(source),
        "output_path": str(target),
        "summary_path": str(summary_target),
        "records_read": len(raw_records),
        "records_processed": len(processed),
        "records_invalid": len(validation.invalid),
        "records_duplicated": len(duplicates),
        "alerts_generated": len(alerts),
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "processing_date": processing_date,
        "dynamodb_written": dynamodb_written,
        "alerts_published": alerts_published,
        "processed_uploaded": processed_uploaded,
        "summary_uploaded": summary_uploaded,
        "metrics_published": False,
    }
    if settings is not None:
        result["metrics_published"] = cloudwatch_metrics.publish_batch_metrics(
            result, settings=settings
        )
    _log(logging.INFO, "batch_processed", **result)
    return result


def lambda_handler(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    """Entry point used by AWS Lambda and by the local runner."""
    started_at = time.perf_counter()

    if not isinstance(event, dict) or not event:
        return _error_result(
            "INVALID_EVENT",
            ValueError("Evento vazio ou inválido."),
            None,
            started_at,
        )

    if event.get("input_path"):
        return process_file(event["input_path"], event.get("output_path"))

    if event.get("local_path"):
        return process_file(event["local_path"], event.get("output_path"))

    records = event.get("Records") or []
    s3_records = [
        record for record in records if isinstance(record, dict) and "s3" in record
    ]
    if s3_records:
        bucket = s3_records[0]["s3"]["bucket"]["name"]
        key = s3_records[0]["s3"]["object"]["key"]
        try:
            payload = s3_storage.read_json(bucket=bucket, key=key)
            transactions = None
            if isinstance(payload, list):
                transactions = payload
            elif isinstance(payload, dict):
                for candidate in ("transactions", "records"):
                    if isinstance(payload.get(candidate), list):
                        transactions = payload[candidate]
                        break
            if transactions is None:
                raise ValueError("Objeto S3 não contém uma lista de transações.")

            tmp_dir = Path(tempfile.gettempdir())
            tmp_name = PurePosixPath(unquote(str(key))).name
            tmp_path = tmp_dir / tmp_name
            tmp_path.write_text(json.dumps(transactions), encoding="utf-8")
            _log(logging.INFO, "s3_direct_read_ok", bucket=bucket, key=key, tmp_path=str(tmp_path))
        except Exception as exc:
            _log(logging.WARNING, "s3_direct_read_fallback", bucket=bucket, key=key, error=str(exc))
            return process_file(
                _resolve_s3_local_path(bucket, key), event.get("output_path")
            )
        return process_file(
            tmp_path, event.get("output_path") or _resolve_tmp_output_path(tmp_path)
        )

    if event.get("bucket") and event.get("key"):
        bucket = event["bucket"]
        key = event["key"]
        try:
            payload = s3_storage.read_json(bucket=bucket, key=key)
            transactions = None
            if isinstance(payload, list):
                transactions = payload
            elif isinstance(payload, dict):
                for candidate in ("transactions", "records"):
                    if isinstance(payload.get(candidate), list):
                        transactions = payload[candidate]
                        break
            if transactions is None:
                raise ValueError("Objeto S3 não contém uma lista de transações.")

            tmp_dir = Path(tempfile.gettempdir())
            tmp_name = PurePosixPath(unquote(str(key))).name
            tmp_path = tmp_dir / tmp_name
            tmp_path.write_text(json.dumps(transactions), encoding="utf-8")
            _log(logging.INFO, "s3_direct_read_ok", bucket=bucket, key=key, tmp_path=str(tmp_path))
        except Exception as exc:
            _log(logging.WARNING, "s3_direct_read_fallback", bucket=bucket, key=key, error=str(exc))
            return process_file(
                _resolve_s3_local_path(bucket, key), event.get("output_path")
            )
        return process_file(
            tmp_path, event.get("output_path") or _resolve_tmp_output_path(tmp_path)
        )

    return _error_result(
        "INVALID_EVENT",
        ValueError("Evento não contém um caminho de arquivo válido para processamento."),
        None,
        started_at,
    )


if __name__ == "__main__":
    result = process_file(None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
