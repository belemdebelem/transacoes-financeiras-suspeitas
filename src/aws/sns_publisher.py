"""Helpers to publish alerts via Amazon SNS.

The integration is optional: when ``SNS_TOPIC_ARN`` is not configured, or
boto3 is missing, the helpers simply skip publishing without breaking the local
pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.aws.clients import sns_client
from src.aws.settings import AwsSettings

logger = logging.getLogger(__name__)


def _format_alert_message(transaction: dict[str, Any]) -> str:
    transaction_id = transaction.get("transaction_id")
    account_id = transaction.get("account_id")
    amount = transaction.get("amount")
    risk_level = transaction.get("risk_level")
    risk_score = transaction.get("risk_score")
    suspicious_reason = transaction.get("suspicious_reason")
    timestamp = transaction.get("timestamp")

    lines = [
        "ALERTA - Transação Financeira Suspeita",
        f"Transaction ID: {transaction_id}",
        f"Account ID: {account_id}",
        f"Timestamp: {timestamp}",
        f"Amount: {amount}",
        f"Risk Level: {risk_level}",
        f"Risk Score: {risk_score}",
        f"Reason: {suspicious_reason}",
    ]
    return "\n".join(str(line) for line in lines)


def publish_alert(
    transaction: dict[str, Any],
    topic_arn: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> bool:
    config = settings or AwsSettings.from_env()
    target_topic = topic_arn or config.sns_topic_arn

    if not target_topic:
        logger.debug("SNS não configurado (SNS_TOPIC_ARN ausente). Pulando alerta.")
        return False

    if not config.alerts_enabled and not topic_arn:
        logger.debug("ALERTS_ENABLED=false. Pulando alerta.")
        return False

    if not isinstance(transaction, dict):
        return False

    try:
        sns = client if client is not None else sns_client(config)
        subject = f"Alerta {transaction.get('risk_level')} - {transaction.get('transaction_id')}"
        message = _format_alert_message(transaction)
        sns.publish(TopicArn=target_topic, Subject=subject[:100], Message=message)
        return True
    except Exception as exc:
        logger.warning("Falha ao publicar alerta no SNS: %s", exc)
        return False


def publish_alerts(
    transactions: list[dict[str, Any]],
    topic_arn: str | None = None,
    settings: AwsSettings | None = None,
    client: Any | None = None,
) -> int:
    if not isinstance(transactions, list) or not transactions:
        return 0

    published = 0
    for transaction in transactions:
        if isinstance(transaction, dict) and transaction.get("alert"):
            if publish_alert(transaction, topic_arn=topic_arn, settings=settings, client=client):
                published += 1
    return published
