"""Schema validation and normalization for raw transaction records.

The validation layer keeps quality problems away from the risk rules: every
record is checked against the model contract, normalized to a predictable
shape, and invalid records are isolated instead of aborting the whole batch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

REQUIRED_FIELDS = (
    "transaction_id",
    "timestamp",
    "account_id",
    "customer_id",
    "transaction_type",
    "amount",
    "currency",
)

OPTIONAL_FIELDS = ("merchant_or_counterparty", "location", "channel")

ALLOWED_TRANSACTION_TYPES = ("DEBIT", "CREDIT", "TRANSFER", "PAYMENT", "PIX")
ALLOWED_CHANNELS = ("APP", "WEB", "ATM", "POS")
ALLOWED_CURRENCIES = ("BRL",)


class ValidationError(ValueError):
    """Raised when a record does not respect the transaction model."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class BatchValidation:
    """Outcome of validating a batch of raw records."""

    valid: list[dict[str, Any]] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_records(self) -> int:
        return len(self.valid) + len(self.invalid)


def _normalize_timestamp(value: Any) -> str:
    """Return an ISO 8601 UTC timestamp, assuming UTC when no offset is given."""
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_amount(value: Any) -> float:
    """Return a positive, finite monetary value rounded to cents."""
    if isinstance(value, bool):
        raise ValueError("amount não pode ser booleano")
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError("amount deve ser um número finito")
    if amount <= 0:
        raise ValueError("amount deve ser maior que zero")
    return round(amount, 2)


def validate_transaction(raw: Any) -> dict[str, Any]:
    """Validate and normalize a single record, raising ValidationError on failure."""
    if not isinstance(raw, dict):
        raise ValidationError(
            [f"registro deve ser um objeto JSON, recebido {type(raw).__name__}"]
        )

    errors: list[str] = []
    normalized: dict[str, Any] = {}

    for name in REQUIRED_FIELDS:
        value = raw.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"campo obrigatório ausente ou vazio: {name}")

    if errors:
        raise ValidationError(errors)

    for name in ("transaction_id", "account_id", "customer_id"):
        normalized[name] = str(raw[name]).strip()

    try:
        normalized["timestamp"] = _normalize_timestamp(raw["timestamp"])
    except (TypeError, ValueError) as exc:
        errors.append(f"timestamp inválido: {raw['timestamp']!r} ({exc})")

    try:
        normalized["amount"] = _normalize_amount(raw["amount"])
    except (TypeError, ValueError) as exc:
        errors.append(f"amount inválido: {raw['amount']!r} ({exc})")

    transaction_type = str(raw["transaction_type"]).strip().upper()
    if transaction_type not in ALLOWED_TRANSACTION_TYPES:
        errors.append(f"transaction_type inválido: {transaction_type!r}")
    else:
        normalized["transaction_type"] = transaction_type

    currency = str(raw["currency"]).strip().upper()
    if currency not in ALLOWED_CURRENCIES:
        errors.append(f"currency inválida: {currency!r}")
    else:
        normalized["currency"] = currency

    if raw.get("channel") is not None:
        channel = str(raw["channel"]).strip().upper()
        if channel not in ALLOWED_CHANNELS:
            errors.append(f"channel inválido: {channel!r}")
        else:
            normalized["channel"] = channel

    for name in ("merchant_or_counterparty", "location"):
        value = raw.get(name)
        if value is not None:
            normalized[name] = str(value).strip()

    if errors:
        raise ValidationError(errors)

    return {
        "transaction_id": normalized["transaction_id"],
        "timestamp": normalized["timestamp"],
        "account_id": normalized["account_id"],
        "customer_id": normalized["customer_id"],
        "transaction_type": normalized["transaction_type"],
        "amount": normalized["amount"],
        "currency": normalized["currency"],
        "merchant_or_counterparty": normalized.get("merchant_or_counterparty"),
        "location": normalized.get("location"),
        "channel": normalized.get("channel"),
    }


def validate_batch(records: Iterable[Any]) -> BatchValidation:
    """Validate a batch, keeping valid and invalid records separated."""
    result = BatchValidation()

    for index, record in enumerate(records):
        try:
            result.valid.append(validate_transaction(record))
        except ValidationError as exc:
            transaction_id = (
                record.get("transaction_id") if isinstance(record, dict) else None
            )
            result.invalid.append(
                {
                    "index": index,
                    "transaction_id": transaction_id,
                    "errors": exc.errors,
                }
            )

    return result
