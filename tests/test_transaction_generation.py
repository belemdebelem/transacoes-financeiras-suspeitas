import json

from src.generator.transactions_generator import (
    DEFAULT_START_DATETIME,
    generate_batch_file,
    generate_transactions,
    save_transactions,
)
from src.processing.validation import REQUIRED_FIELDS, validate_batch
from src.rules.risk_rules import apply_risk_rules


def test_generate_transactions_has_expected_shape():
    transactions = generate_transactions(total_transactions=60, seed=11)

    assert len(transactions) == 60
    assert all("transaction_id" in tx for tx in transactions)
    assert all(
        "account_id" in tx and tx["account_id"].startswith("ACC_") for tx in transactions
    )
    assert all("amount" in tx and tx["amount"] > 0 for tx in transactions)


def test_generated_transactions_have_all_required_fields():
    transactions = generate_transactions(total_transactions=60, seed=11)

    for transaction in transactions:
        assert all(field in transaction for field in REQUIRED_FIELDS)


def test_generation_is_deterministic():
    first = generate_transactions(total_transactions=60, seed=11)
    second = generate_transactions(total_transactions=60, seed=11)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_different_seeds_produce_different_data():
    first = generate_transactions(total_transactions=60, seed=11)
    second = generate_transactions(total_transactions=60, seed=12)

    assert json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True)


def test_transaction_ids_are_unique():
    transactions = generate_transactions(total_transactions=120, seed=7)

    identifiers = [transaction["transaction_id"] for transaction in transactions]

    assert len(set(identifiers)) == len(identifiers)


def test_generated_data_has_no_status_field():
    transactions = generate_transactions(total_transactions=60, seed=11)

    assert all("status" not in transaction for transaction in transactions)


def test_generated_timestamps_use_the_fixed_anchor():
    transactions = generate_transactions(total_transactions=30, seed=11)

    timestamps = sorted(transaction["timestamp"] for transaction in transactions)

    assert timestamps[0] >= DEFAULT_START_DATETIME.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_generated_data_contains_every_supported_dimension():
    transactions = generate_transactions(total_transactions=200, seed=42)

    assert {"DEBIT", "CREDIT", "TRANSFER", "PAYMENT", "PIX"} <= {
        transaction["transaction_type"] for transaction in transactions
    }
    assert {"APP", "WEB", "ATM", "POS"} <= {
        transaction["channel"] for transaction in transactions
    }


def test_generated_data_exercises_the_accumulation_rule():
    transactions = generate_transactions(total_transactions=200, seed=42)

    processed = apply_risk_rules(transactions)
    bursts = [
        item
        for item in processed
        if "MULTIPLE_TRANSACTIONS_WINDOW" in (item["suspicious_reason"] or "")
    ]

    assert bursts


def test_generated_data_produces_every_risk_level():
    transactions = generate_transactions(total_transactions=200, seed=42)

    processed = apply_risk_rules(transactions)
    levels = {item["risk_level"] for item in processed}

    assert {"NORMAL", "ATTENTION", "SUSPECT", "CRITICAL"} <= levels


def test_generated_data_passes_the_schema_validation():
    transactions = generate_transactions(total_transactions=200, seed=42)

    result = validate_batch(transactions)

    assert result.invalid == []
    assert len(result.valid) == 200


def test_save_transactions_to_json(tmp_path):
    transactions = generate_transactions(total_transactions=5, seed=3)
    output_path = tmp_path / "transactions.json"

    saved_path = save_transactions(transactions, output_path)

    assert saved_path.exists()
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert len(payload) == 5


def test_generate_batch_file_writes_the_raw_folder(tmp_path):
    output_path = generate_batch_file(output_dir=tmp_path, total_transactions=25, seed=5)

    assert output_path.exists()
    assert output_path.parent == tmp_path
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload) == 25
