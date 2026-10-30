import json

from src.monitoring.operational_metrics import export_summary, summarize_transactions


def _transaction(**overrides):
    transaction = {
        "transaction_id": "txn_0001",
        "timestamp": "2026-10-05T12:00:00Z",
        "account_id": "ACC_0001",
        "customer_id": "CUST_0001",
        "transaction_type": "DEBIT",
        "amount": 1000.0,
        "currency": "BRL",
        "location": "SÃO PAULO",
        "channel": "APP",
        "risk_score": 0,
        "risk_level": "NORMAL",
        "suspicious_reason": None,
        "alert": False,
    }
    transaction.update(overrides)
    return transaction


def _sample_batch():
    return [
        _transaction(),
        _transaction(
            transaction_id="txn_0002",
            account_id="ACC_0002",
            amount=18000.0,
            risk_score=50,
            risk_level="SUSPECT",
            suspicious_reason="HIGH_AMOUNT",
            alert=True,
        ),
        _transaction(
            transaction_id="txn_0003",
            account_id="ACC_0001",
            amount=25000.0,
            timestamp="2026-10-06T09:00:00Z",
            risk_score=85,
            risk_level="CRITICAL",
            suspicious_reason="HIGH_AMOUNT; CRITICAL_COMBINATION",
            alert=True,
        ),
    ]


def test_summary_counts_match_the_processed_data():
    batch = _sample_batch()

    summary = summarize_transactions(batch)

    assert summary["total_transactions"] == 3
    assert summary["alert_count"] == 2
    assert summary["alert_rate_percent"] == 66.67
    assert summary["alerted_amount"] == 43000.0


def test_summary_groups_alerts_by_risk_level():
    summary = summarize_transactions(_sample_batch())

    assert summary["risk_levels"] == {"NORMAL": 1, "SUSPECT": 1, "CRITICAL": 1}


def test_summary_counts_each_alert_reason():
    summary = summarize_transactions(_sample_batch())

    assert summary["alert_reasons"] == {"HIGH_AMOUNT": 2, "CRITICAL_COMBINATION": 1}
    assert summary["top_alert_rule"] == "HIGH_AMOUNT"


def test_summary_groups_alerts_by_account():
    summary = summarize_transactions(_sample_batch())

    assert summary["alerts_by_account"] == {"ACC_0001": 1, "ACC_0002": 1}


def test_summary_groups_alerts_by_period():
    summary = summarize_transactions(_sample_batch())

    assert summary["alerts_by_period"] == {"2026-10-05": 1, "2026-10-06": 1}


def test_summary_reports_the_invalid_records():
    summary = summarize_transactions(_sample_batch(), invalid_records=3)

    assert summary["records_invalid"] == 3


def test_summary_of_an_empty_batch():
    summary = summarize_transactions([])

    assert summary["total_transactions"] == 0
    assert summary["alert_count"] == 0
    assert summary["alert_rate_percent"] == 0.0
    assert summary["top_alert_rule"] is None
    assert summary["alerts_by_account"] == {}


def test_export_summary_writes_the_summary_file(tmp_path):
    output_path = tmp_path / "summary.json"

    written = export_summary(_sample_batch(), output_path, invalid_records=1)

    assert written.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["total_transactions"] == 3
    assert payload["records_invalid"] == 1
