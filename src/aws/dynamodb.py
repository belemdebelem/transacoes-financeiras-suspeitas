"""Helpers to persist processed transactions into DynamoDB.

The integration is optional: when ``DYNAMODB_TABLE`` is not configured, or
boto3 is missing, the helpers simply skip writing without breaking the local
pipeline.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from src.aws.clients import _boto3
from src.aws.settings import AwsSettings

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Any:
    """Convert floats to Decimal to be safe with boto3 DynamoDB."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value: Any) -> Any:
    """Convert Decimal back to int/float for JSON/logging if ever needed."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _ensure_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _build_item(transaction: dict[str, Any], processed_at: str | None = None) -> dict[str, Any]:
    """Normalize a processed transaction into a DynamoDB item."""
    item: dict[str, Any] = {}

    transaction_id = _ensure_string(transaction.get("transaction_id"))
    if not transaction_id:
        raise ValueError("Registro processado sem 'transaction_id' não pode ser gravado no DynamoDB.")

    item["transaction_id"] = transaction_id

    for key in (
        "timestamp",
        "account_id",
        "customer_id",
        "transaction_type",
        "currency",
        "merchant_or_counterparty",
        "location",
        "channel",
    ):
        if key in transaction and transaction[key] is not None:
            item[key] = _ensure_string(transaction[key])

    timestamp = transaction.get("timestamp")
    if timestamp:
        # Required by account_id-index (account_id + event_date).
        item["event_date"] = _ensure_string(timestamp)[:10]

    if "amount" in transaction and transaction["amount"] is not None:
        item["amount"] = _to_decimal(transaction["amount"])
    if "risk_score" in transaction and transaction["risk_score"] is not None:
        item["risk_score"] = _to_decimal(transaction["risk_score"])

    if "risk_level" in transaction and transaction["risk_level"] is not None:
        item["risk_level"] = _ensure_string(transaction["risk_level"])
    if "suspicious_reason" in transaction and transaction["suspicious_reason"] is not None:
        item["suspicious_reason"] = _ensure_string(transaction["suspicious_reason"])
    if "alert" in transaction:
        item["alert"] = bool(transaction["alert"])

    if processed_at:
        item["processed_at"] = processed_at

    return _to_decimal(item)


def put_processed_transactions(
    transactions: list[dict[str, Any]],
    table_name: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
    processed_at: str | None = None,
) -> int:
    """Put processed transactions into DynamoDB.

    Uses batch_writer when available. Returns number of items written.
    Skips silently if table is not configured.
    """
    config = settings or AwsSettings.from_env()
    target_table = table_name or config.dynamodb_table

    if not target_table:
        logger.debug("DynamoDB não configurado (DYNAMODB_TABLE ausente). Pulando persistência.")
        return 0

    if not isinstance(transactions, list) or not transactions:
        return 0

    written = 0
    try:
        boto3 = _boto3()
        dynamodb = boto3.resource("dynamodb", region_name=config.region)
        table = dynamodb.Table(target_table)
        with table.batch_writer(overwrite_by_pkeys=["transaction_id"]) as batch:
            for transaction in transactions:
                if not isinstance(transaction, dict):
                    continue
                try:
                    item = _build_item(transaction, processed_at=processed_at)
                    batch.put_item(Item=item)
                    written += 1
                except Exception as exc:
                    logger.warning("Falha ao preparar item para DynamoDB: %s", exc)
                    continue
        return written
    except Exception as exc:
        logger.warning("Falha ao gravar no DynamoDB: %s", exc)
        return written


def put_processed_transaction(
    transaction: dict[str, Any],
    table_name: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
    processed_at: str | None = None,
) -> bool:
    """Put a single processed transaction into DynamoDB."""
    count = put_processed_transactions(
        [transaction],
        table_name=table_name,
        settings=settings,
        client=client,
        processed_at=processed_at,
    )
    return count >= 1
