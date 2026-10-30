"""Business rules that classify transactions and explain why they were flagged.

Four rules are applied to every record:

1. ``HIGH_AMOUNT`` - the transaction amount is above the high amount threshold.
2. ``ATM_HIGH_AMOUNT`` - a large amount was made through an ATM.
3. ``MULTIPLE_TRANSACTIONS_WINDOW`` - the account accumulated several
   transactions in a short window, above the accumulated amount threshold.
4. ``UNUSUAL_CHANNEL`` / ``UNUSUAL_LOCATION`` - the transaction happened in a
   channel or place that is atypical for the customer profile, with a high
   amount.

Scores accumulate across rules and drive the classification band
(``NORMAL``, ``ATTENTION``, ``SUSPECT``, ``CRITICAL``).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from src.rules.rules_config import DEFAULT_CONFIG, RuleConfig

RISK_LEVELS = ("NORMAL", "ATTENTION", "SUSPECT", "CRITICAL")

REASON_HIGH_AMOUNT = "HIGH_AMOUNT"
REASON_ATM_HIGH_AMOUNT = "ATM_HIGH_AMOUNT"
REASON_BURST = "MULTIPLE_TRANSACTIONS_WINDOW"
REASON_UNUSUAL_CHANNEL = "UNUSUAL_CHANNEL"
REASON_UNUSUAL_LOCATION = "UNUSUAL_LOCATION"
REASON_COMBINATION = "CRITICAL_COMBINATION"


def _parse_amount(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return amount if math.isfinite(amount) else 0.0


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _account_of(transaction: dict[str, Any]) -> str:
    return str(transaction.get("account_id") or "UNKNOWN")


def _build_baselines(
    transactions: list[dict[str, Any]],
) -> dict[str, dict[str, Counter]]:
    """Count the channel and location usage of every account in the batch."""
    channels: dict[str, Counter] = defaultdict(Counter)
    locations: dict[str, Counter] = defaultdict(Counter)

    for transaction in transactions:
        account = _account_of(transaction)
        channel = transaction.get("channel")
        if channel:
            channels[account][str(channel)] += 1
        location = transaction.get("location")
        if location:
            locations[account][str(location)] += 1

    return {
        account: {
            "channels": channels.get(account, Counter()),
            "locations": locations.get(account, Counter()),
        }
        for account in set(channels) | set(locations)
    }


def _is_unusual(
    counts: Counter,
    total: int,
    current: str,
    share_limit: float,
    config: RuleConfig,
) -> bool:
    """Check whether a channel or location is atypical for the account."""
    if total < config.min_baseline_samples:
        return False
    if not counts:
        return False
    if max(counts.values()) / total < config.dominance_threshold:
        return False
    return counts.get(current, 0) / total < share_limit


def _detect_burst_transactions(
    transactions: list[dict[str, Any]],
    config: RuleConfig,
) -> set[str]:
    """Return the ids of every transaction inside a suspicious accumulation window.

    The window is a sliding interval of ``burst_window_seconds`` that ends at
    the current transaction, so only transactions that already happened are
    considered.
    """
    by_account: dict[str, list[tuple[datetime, float, str]]] = defaultdict(list)

    for transaction in transactions:
        timestamp = _parse_timestamp(transaction.get("timestamp"))
        if timestamp is None:
            continue
        by_account[_account_of(transaction)].append(
            (
                timestamp,
                _parse_amount(transaction.get("amount")),
                str(transaction.get("transaction_id") or ""),
            )
        )

    flagged: set[str] = set()

    for entries in by_account.values():
        entries.sort(key=lambda item: item[0])
        left = 0
        window_amount = 0.0

        for right, (timestamp, amount, _) in enumerate(entries):
            window_amount += amount
            while (
                left < right
                and (timestamp - entries[left][0]).total_seconds()
                > config.burst_window_seconds
            ):
                window_amount -= entries[left][1]
                left += 1

            count = right - left + 1
            if (
                count >= config.burst_min_transactions
                and window_amount > config.burst_amount_threshold
            ):
                for index in range(left, right + 1):
                    flagged.add(entries[index][2])

    return flagged


def apply_risk_rules(
    transactions: list[dict[str, Any]],
    config: RuleConfig = DEFAULT_CONFIG,
) -> list[dict[str, Any]]:
    """Classify every transaction and return the enriched records."""
    records = [transaction for transaction in transactions if isinstance(transaction, dict)]
    baselines = _build_baselines(records)
    burst_ids = _detect_burst_transactions(records, config)

    processed: list[dict[str, Any]] = []

    for transaction in records:
        amount = _parse_amount(transaction.get("amount"))
        account = _account_of(transaction)
        channel = transaction.get("channel")
        location = transaction.get("location")

        reasons: list[str] = []
        score = 0

        high_amount = amount > config.high_amount_threshold
        if high_amount:
            score += config.high_amount_score
            reasons.append(REASON_HIGH_AMOUNT)

        atm_high_amount = (
            channel == "ATM" and amount >= config.atm_amount_threshold
        )
        if atm_high_amount:
            score += config.atm_score
            reasons.append(REASON_ATM_HIGH_AMOUNT)

        baseline = baselines.get(account, {"channels": Counter(), "locations": Counter()})
        channels = baseline["channels"]
        locations = baseline["locations"]

        unusual_channel = (
            channel is not None
            and channel != "ATM"
            and amount >= config.unusual_amount_threshold
            and _is_unusual(
                channels,
                sum(channels.values()),
                str(channel),
                config.unusual_channel_share,
                config,
            )
        )
        if unusual_channel:
            score += config.unusual_channel_score
            reasons.append(REASON_UNUSUAL_CHANNEL)

        unusual_location = (
            location is not None
            and amount >= config.unusual_amount_threshold
            and _is_unusual(
                locations,
                sum(locations.values()),
                str(location),
                config.unusual_location_share,
                config,
            )
        )
        if unusual_location:
            score += config.unusual_location_score
            reasons.append(REASON_UNUSUAL_LOCATION)

        burst = str(transaction.get("transaction_id") or "") in burst_ids
        if burst:
            score += config.burst_score
            reasons.append(REASON_BURST)

        severe_combination = (
            high_amount
            and (atm_high_amount or unusual_channel)
            and (burst or unusual_location)
        )
        if severe_combination:
            score += config.combination_score
            reasons.append(REASON_COMBINATION)

        score = max(0, min(config.max_score, score))

        record = dict(transaction)
        record["risk_score"] = score
        record["risk_level"] = config.risk_level_for(score)
        record["suspicious_reason"] = "; ".join(reasons) if reasons else None
        record["alert"] = score >= config.alert_score
        processed.append(record)

    return processed
