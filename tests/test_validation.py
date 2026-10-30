import pytest

from src.processing.validation import (
    ALLOWED_CHANNELS,
    ALLOWED_TRANSACTION_TYPES,
    ValidationError,
    validate_batch,
    validate_transaction,
)


def test_valid_transaction_is_accepted(valid_transaction):
    normalized = validate_transaction(valid_transaction)

    assert normalized["transaction_id"] == "txn_000001"
    assert normalized["timestamp"] == "2026-10-05T12:00:00Z"
    assert normalized["amount"] == 1500.0


@pytest.mark.parametrize(
    "field",
    [
        "transaction_id",
        "timestamp",
        "account_id",
        "customer_id",
        "transaction_type",
        "amount",
        "currency",
    ],
)
def test_missing_required_field_is_rejected(valid_transaction, field):
    valid_transaction.pop(field)

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any(field in message for message in error.value.errors)


def test_blank_required_field_is_rejected(valid_transaction):
    valid_transaction["account_id"] = "   "

    with pytest.raises(ValidationError):
        validate_transaction(valid_transaction)


@pytest.mark.parametrize("amount", ["abc", None, -10, 0, True, float("nan")])
def test_invalid_amount_is_rejected(valid_transaction, amount):
    valid_transaction["amount"] = amount

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any("amount" in message for message in error.value.errors)


@pytest.mark.parametrize(
    "timestamp", ["05/10/2026", "ontem", "", "2026-13-45T00:00:00Z"]
)
def test_invalid_timestamp_is_rejected(valid_transaction, timestamp):
    valid_transaction["timestamp"] = timestamp

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any("timestamp" in message for message in error.value.errors)


def test_invalid_transaction_type_is_rejected(valid_transaction):
    valid_transaction["transaction_type"] = "TRANSFERENCIA"

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any("transaction_type" in message for message in error.value.errors)


def test_invalid_channel_is_rejected(valid_transaction):
    valid_transaction["channel"] = "TELEFONE"

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any("channel" in message for message in error.value.errors)


def test_invalid_currency_is_rejected(valid_transaction):
    valid_transaction["currency"] = "USD"

    with pytest.raises(ValidationError) as error:
        validate_transaction(valid_transaction)

    assert any("currency" in message for message in error.value.errors)


def test_transaction_type_is_uppercased(valid_transaction):
    valid_transaction["transaction_type"] = "pix"

    normalized = validate_transaction(valid_transaction)

    assert normalized["transaction_type"] == "PIX"
    assert normalized["transaction_type"] in ALLOWED_TRANSACTION_TYPES


def test_channel_is_uppercased(valid_transaction):
    valid_transaction["channel"] = "app"

    normalized = validate_transaction(valid_transaction)

    assert normalized["channel"] == "APP"
    assert normalized["channel"] in ALLOWED_CHANNELS


def test_amount_is_rounded_to_cents(valid_transaction):
    valid_transaction["amount"] = 1234.5678

    normalized = validate_transaction(valid_transaction)

    assert normalized["amount"] == 1234.57


def test_timestamp_without_timezone_is_treated_as_utc(valid_transaction):
    valid_transaction["timestamp"] = "2026-10-05T12:00:00"

    normalized = validate_transaction(valid_transaction)

    assert normalized["timestamp"] == "2026-10-05T12:00:00Z"


def test_unknown_fields_are_dropped(valid_transaction):
    valid_transaction["unexpected"] = "value"

    normalized = validate_transaction(valid_transaction)

    assert "unexpected" not in normalized


def test_non_object_record_is_rejected():
    with pytest.raises(ValidationError):
        validate_transaction(["not", "a", "record"])


def test_batch_keeps_valid_records_and_isolates_invalid_ones(valid_transaction):
    invalid = dict(valid_transaction, amount=-1, transaction_id="txn_bad")
    records = [valid_transaction, invalid, dict(valid_transaction, transaction_id="txn_2")]

    result = validate_batch(records)

    assert len(result.valid) == 2
    assert len(result.invalid) == 1
    assert result.total_records == 3
    assert result.invalid[0]["transaction_id"] == "txn_bad"
    assert result.invalid[0]["errors"]


def test_batch_reports_every_error_of_a_record(valid_transaction):
    invalid = dict(valid_transaction, amount="abc", timestamp="ontem")

    result = validate_batch([invalid])

    assert len(result.invalid) == 1
    assert len(result.invalid[0]["errors"]) == 2
