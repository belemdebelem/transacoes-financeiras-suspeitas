"""Operational summaries for processed transactions and risk alerts."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

TOP_ACCOUNTS = 10


def _parse_amount(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return amount if math.isfinite(amount) else 0.0


def _period_of(transaction: dict[str, Any]) -> str:
    timestamp = str(transaction.get("timestamp") or "")
    return timestamp[:10] if len(timestamp) >= 10 else "UNKNOWN"


def summarize_transactions(
    transactions: list[dict[str, Any]],
    invalid_records: int = 0,
) -> dict[str, Any]:
    """Build the operational summary of a processed batch."""
    if not transactions:
        return {
            "total_transactions": 0,
            "alert_count": 0,
            "alert_rate_percent": 0.0,
            "alerted_amount": 0.0,
            "risk_levels": {},
            "alert_reasons": {},
            "top_alert_rule": None,
            "alerts_by_account": {},
            "alerts_by_period": {},
            "records_invalid": invalid_records,
        }

    risk_levels = dict(Counter(item.get("risk_level", "NORMAL") for item in transactions))
    alerts = [item for item in transactions if item.get("alert")]
    alert_reasons = Counter(
        reason.strip()
        for item in alerts
        for reason in (item.get("suspicious_reason") or "").split(";")
        if reason.strip()
    )
    alerted_amount = sum(_parse_amount(item.get("amount")) for item in alerts)
    alerts_by_account = dict(
        Counter(str(item.get("account_id") or "UNKNOWN") for item in alerts).most_common(
            TOP_ACCOUNTS
        )
    )
    alerts_by_period = dict(
        sorted(Counter(_period_of(item) for item in alerts).items())
    )

    return {
        "total_transactions": len(transactions),
        "alert_count": len(alerts),
        "alert_rate_percent": round(len(alerts) / len(transactions) * 100, 2),
        "alerted_amount": round(alerted_amount, 2),
        "risk_levels": risk_levels,
        "alert_reasons": dict(alert_reasons),
        "top_alert_rule": alert_reasons.most_common(1)[0][0] if alert_reasons else None,
        "alerts_by_account": alerts_by_account,
        "alerts_by_period": alerts_by_period,
        "records_invalid": invalid_records,
    }


def export_summary(
    transactions: list[dict[str, Any]],
    output_path: str | Path,
    invalid_records: int = 0,
) -> Path:
    """Write the operational summary of a processed batch to disk."""
    summary = summarize_transactions(transactions, invalid_records=invalid_records)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return target
