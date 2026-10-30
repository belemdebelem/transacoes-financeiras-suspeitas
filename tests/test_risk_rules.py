from src.rules.risk_rules import apply_risk_rules
from src.rules.rules_config import RuleConfig


def _transaction(**overrides):
    transaction = {
        "transaction_id": "txn_0001",
        "timestamp": "2026-10-05T12:00:00Z",
        "account_id": "ACC_0001",
        "customer_id": "CUST_0001",
        "transaction_type": "DEBIT",
        "amount": 1500.0,
        "currency": "BRL",
        "merchant_or_counterparty": "Loja A",
        "location": "SÃO PAULO",
        "channel": "APP",
    }
    transaction.update(overrides)
    return transaction


def _burst(account_id="ACC_0001", channel="APP", amounts=(2500.0, 2700.0, 3000.0)):
    timestamps = (
        "2026-10-05T12:00:00Z",
        "2026-10-05T12:04:00Z",
        "2026-10-05T12:08:00Z",
    )
    return [
        _transaction(
            transaction_id=f"txn_b{index + 1}",
            timestamp=timestamps[index],
            account_id=account_id,
            channel=channel,
            amount=amounts[index],
        )
        for index in range(len(amounts))
    ]


def test_normal_transaction_is_not_flagged():
    processed = apply_risk_rules([_transaction()])

    assert processed[0]["alert"] is False
    assert processed[0]["risk_level"] == "NORMAL"
    assert processed[0]["risk_score"] == 0
    assert processed[0]["suspicious_reason"] is None


def test_high_amount_transaction_is_flagged():
    processed = apply_risk_rules([_transaction(amount=18000.0)])

    assert processed[0]["alert"] is True
    assert processed[0]["risk_level"] in {"SUSPECT", "CRITICAL"}
    assert "HIGH_AMOUNT" in (processed[0]["suspicious_reason"] or "")


def test_multiple_transactions_in_short_window_are_detected():
    processed = apply_risk_rules(_burst())

    assert any(item["alert"] for item in processed)
    assert any(item["risk_level"] == "SUSPECT" for item in processed)
    assert all(
        "MULTIPLE_TRANSACTIONS_WINDOW" in (item["suspicious_reason"] or "")
        for item in processed
    )


def test_transactions_outside_the_window_are_not_flagged():
    late = _transaction(
        transaction_id="txn_late", timestamp="2026-10-05T12:40:00Z", amount=9000.0
    )

    processed = apply_risk_rules(_burst() + [late])

    assert "MULTIPLE_TRANSACTIONS_WINDOW" not in (processed[-1]["suspicious_reason"] or "")


def test_window_is_not_symmetric():
    earlier = _transaction(
        transaction_id="txn_early", timestamp="2026-10-05T11:52:00Z", amount=9000.0
    )

    processed = apply_risk_rules([earlier] + _burst())

    assert "MULTIPLE_TRANSACTIONS_WINDOW" not in (processed[0]["suspicious_reason"] or "")


def test_unusual_channel_is_detected_for_the_customer_profile():
    baseline = [
        _transaction(
            transaction_id=f"txn_base_{index}",
            timestamp=f"2026-10-0{index + 1}T09:00:00Z",
            amount=120.0,
            channel="APP",
        )
        for index in range(8)
    ]
    unusual = _transaction(
        transaction_id="txn_web",
        timestamp="2026-10-09T09:00:00Z",
        amount=6000.0,
        channel="WEB",
    )

    processed = apply_risk_rules(baseline + [unusual])

    assert "UNUSUAL_CHANNEL" in (processed[-1]["suspicious_reason"] or "")
    assert processed[-1]["risk_level"] == "ATTENTION"
    assert processed[-1]["alert"] is False


def test_channel_is_not_unusual_without_a_clear_customer_pattern():
    baseline = [
        _transaction(
            transaction_id=f"txn_base_{index}",
            timestamp=f"2026-10-{index + 1:02d}T09:00:00Z",
            amount=120.0,
            channel=("APP", "WEB", "POS")[index % 3],
        )
        for index in range(9)
    ]
    tested = _transaction(
        transaction_id="txn_tested",
        timestamp="2026-10-10T09:00:00Z",
        amount=6000.0,
        channel="ATM",
    )

    processed = apply_risk_rules(baseline + [tested])

    assert "UNUSUAL_CHANNEL" not in (processed[-1]["suspicious_reason"] or "")


def test_unusual_location_is_detected_for_the_customer_profile():
    baseline = [
        _transaction(
            transaction_id=f"txn_base_{index}",
            timestamp=f"2026-10-{index + 1:02d}T09:00:00Z",
            amount=120.0,
            location="SÃO PAULO",
        )
        for index in range(8)
    ]
    unusual = _transaction(
        transaction_id="txn_away",
        timestamp="2026-10-09T09:00:00Z",
        amount=6000.0,
        location="RECIFE",
    )

    processed = apply_risk_rules(baseline + [unusual])

    assert "UNUSUAL_LOCATION" in (processed[-1]["suspicious_reason"] or "")


def test_severe_combination_is_classified_as_critical():
    transactions = [
        _transaction(
            transaction_id="txn_c1",
            timestamp="2026-10-05T12:00:00Z",
            amount=12000.0,
            channel="ATM",
        ),
        _transaction(
            transaction_id="txn_c2",
            timestamp="2026-10-05T12:03:00Z",
            amount=3000.0,
            channel="ATM",
        ),
        _transaction(
            transaction_id="txn_c3",
            timestamp="2026-10-05T12:06:00Z",
            amount=3000.0,
            channel="ATM",
        ),
    ]

    processed = apply_risk_rules(transactions)

    assert processed[0]["risk_level"] == "CRITICAL"
    assert processed[0]["risk_score"] == 100
    assert "CRITICAL_COMBINATION" in (processed[0]["suspicious_reason"] or "")


def test_score_is_capped():
    transactions = [
        _transaction(
            transaction_id="txn_x1",
            timestamp="2026-10-05T12:00:00Z",
            amount=30000.0,
            channel="ATM",
            location="RECIFE",
        ),
        _transaction(
            transaction_id="txn_x2",
            timestamp="2026-10-05T12:02:00Z",
            amount=30000.0,
            channel="ATM",
            location="RECIFE",
        ),
        _transaction(
            transaction_id="txn_x3",
            timestamp="2026-10-05T12:04:00Z",
            amount=30000.0,
            channel="ATM",
            location="RECIFE",
        ),
    ]

    processed = apply_risk_rules(transactions)

    assert all(item["risk_score"] <= 100 for item in processed)


def test_custom_config_changes_the_thresholds():
    config = RuleConfig(high_amount_threshold=1000.0)

    processed = apply_risk_rules([_transaction(amount=1500.0)], config)

    assert processed[0]["alert"] is True
    assert "HIGH_AMOUNT" in (processed[0]["suspicious_reason"] or "")


def test_custom_config_can_raise_the_alert_threshold():
    config = RuleConfig(alert_score=80)

    processed = apply_risk_rules([_transaction(amount=18000.0)], config)

    assert processed[0]["risk_level"] == "SUSPECT"
    assert processed[0]["alert"] is False


def test_risk_level_bands():
    config = RuleConfig()

    assert config.risk_level_for(0) == "NORMAL"
    assert config.risk_level_for(19) == "NORMAL"
    assert config.risk_level_for(20) == "ATTENTION"
    assert config.risk_level_for(49) == "ATTENTION"
    assert config.risk_level_for(50) == "SUSPECT"
    assert config.risk_level_for(74) == "SUSPECT"
    assert config.risk_level_for(75) == "CRITICAL"


def test_invalid_timestamp_does_not_break_the_classification():
    processed = apply_risk_rules([_transaction(timestamp="05/10/2026")])

    assert processed[0]["risk_level"] == "NORMAL"


def test_original_fields_are_preserved():
    processed = apply_risk_rules([_transaction()])

    assert processed[0]["transaction_id"] == "txn_0001"
    assert processed[0]["merchant_or_counterparty"] == "Loja A"
    assert processed[0]["currency"] == "BRL"


def test_empty_batch_returns_empty_result():
    assert apply_risk_rules([]) == []
