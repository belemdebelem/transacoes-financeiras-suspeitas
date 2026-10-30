import json

from src.processing.lambda_handler import lambda_handler, process_file


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


def _write(tmp_path, records, name="raw_transactions.json"):
    input_path = tmp_path / name
    input_path.write_text(json.dumps(records), encoding="utf-8")
    return input_path


def test_process_file_creates_processed_output(tmp_path):
    input_path = _write(
        tmp_path,
        [
            _transaction(),
            _transaction(transaction_id="txn_2", amount=25000.0, channel="ATM"),
        ],
    )
    output_path = tmp_path / "processed" / "processed_transactions.json"

    result = process_file(input_path, output_path)

    assert result["status"] == "success"
    assert output_path.exists()
    processed = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(processed) == 2
    assert any(item["alert"] for item in processed)


def test_process_file_reports_the_operational_counters(tmp_path):
    input_path = _write(
        tmp_path,
        [
            _transaction(),
            _transaction(transaction_id="txn_2", amount=-10),
            _transaction(transaction_id="txn_3", amount=25000.0, channel="ATM"),
        ],
    )

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["records_read"] == 3
    assert result["records_processed"] == 2
    assert result["records_invalid"] == 1
    assert result["alerts_generated"] == 1
    assert result["duration_ms"] >= 0
    assert result["processing_date"].endswith("Z")


def test_process_file_writes_the_operational_summary(tmp_path):
    input_path = _write(tmp_path, [_transaction()])
    output_path = tmp_path / "processed.json"

    result = process_file(input_path, output_path)

    summary_path = tmp_path / "processed_summary.json"
    assert result["summary_path"] == str(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total_transactions"] == 1


def test_process_file_removes_duplicated_transaction_ids(tmp_path):
    input_path = _write(
        tmp_path,
        [_transaction(), _transaction(amount=9000.0), _transaction(transaction_id="txn_2")],
    )

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["records_read"] == 3
    assert result["records_processed"] == 2
    assert result["records_duplicated"] == 1


def test_process_file_returns_error_for_a_missing_file(tmp_path):
    result = process_file(tmp_path / "missing.json", tmp_path / "processed.json")

    assert result["status"] == "error"
    assert result["error_code"] == "FILE_NOT_FOUND"
    assert not (tmp_path / "processed.json").exists()


def test_process_file_returns_error_for_an_invalid_json(tmp_path):
    input_path = tmp_path / "broken.json"
    input_path.write_text("{ isso nao eh json ", encoding="utf-8")

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_JSON"


def test_process_file_returns_error_for_an_invalid_payload(tmp_path):
    input_path = tmp_path / "payload.json"
    input_path.write_text(json.dumps({"unexpected": "key"}), encoding="utf-8")

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_PAYLOAD"


def test_processed_record_keeps_the_whole_transaction(tmp_path):
    input_path = _write(tmp_path, [_transaction()])
    output_path = tmp_path / "processed.json"

    process_file(input_path, output_path)

    processed = json.loads(output_path.read_text(encoding="utf-8"))[0]
    assert processed["transaction_id"] == "txn_0001"
    assert processed["merchant_or_counterparty"] == "Loja A"
    assert processed["risk_level"] == "NORMAL"
    assert processed["alert"] is False


def test_lambda_handler_accepts_an_input_path_event(tmp_path):
    input_path = _write(tmp_path, [_transaction()])

    result = lambda_handler(
        {
            "input_path": str(input_path),
            "output_path": str(tmp_path / "out.json"),
        }
    )

    assert result["status"] == "success"
    assert result["records_processed"] == 1


def test_lambda_handler_accepts_an_s3_event(tmp_path, monkeypatch):
    input_path = _write(tmp_path, [_transaction()], name="transactions_20261005.json")
    monkeypatch.setenv("LOCAL_S3_MIRROR", str(tmp_path))

    result = lambda_handler(
        {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "raw-bucket"},
                        "object": {"key": "raw-data/2026/10/05/transactions_20261005.json"},
                    }
                }
            ]
        }
    )

    assert result["status"] == "success"
    assert result["input_path"] == str(input_path)


def test_lambda_handler_decodes_the_s3_key(tmp_path, monkeypatch):
    input_path = _write(tmp_path, [_transaction()], name="transacoes com espaco.json")
    monkeypatch.setenv("LOCAL_S3_MIRROR", str(tmp_path))

    result = lambda_handler(
        {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "raw-bucket"},
                        "object": {"key": "raw-data/transacoes%20com%20espaco.json"},
                    }
                }
            ]
        }
    )

    assert result["status"] == "success"
    assert result["input_path"] == str(input_path)


def test_lambda_handler_writes_s3_event_outputs_to_tmp(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("src.processing.lambda_handler.tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "src.processing.lambda_handler.s3_storage.read_json",
        lambda **kwargs: [_transaction()],
    )

    result = lambda_handler(
        {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "raw-bucket"},
                        "object": {"key": "raw-data/transactions.json"},
                    }
                }
            ]
        }
    )

    output_path = tmp_path / "processed" / "transactions_processed.json"
    assert result["status"] == "success"
    assert result["output_path"] == str(output_path)
    assert output_path.exists()


def test_lambda_handler_returns_error_for_an_unknown_event():
    result = lambda_handler({"unexpected": "payload"})

    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_EVENT"


def test_lambda_handler_returns_error_for_an_empty_event():
    result = lambda_handler({})

    assert result["status"] == "error"
    assert result["error_code"] == "INVALID_EVENT"


def test_process_file_persists_and_publishes_when_configured(tmp_path, monkeypatch):
    input_path = _write(
        tmp_path,
        [
            _transaction(),
            _transaction(transaction_id="txn_0002", amount=25000.0, channel="ATM"),
        ],
    )
    captured = {}

    def fake_persist(transactions, settings=None, processed_at=None):
        captured["dynamodb_transactions"] = transactions
        captured["dynamodb_settings"] = settings
        captured["processed_at"] = processed_at
        return len(transactions)

    def fake_publish(transactions, settings=None, client=None):
        captured["sns_transactions"] = transactions
        captured["sns_settings"] = settings
        return sum(1 for transaction in transactions if transaction.get("alert"))

    monkeypatch.setattr(
        "src.processing.lambda_handler.dynamodb_storage.put_processed_transactions",
        fake_persist,
    )
    monkeypatch.setattr(
        "src.processing.lambda_handler.sns_publisher.publish_alerts",
        fake_publish,
    )
    monkeypatch.setenv("DYNAMODB_TABLE", "table-test")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-2:123:topic-test")
    monkeypatch.setenv("ALERTS_ENABLED", "true")
    monkeypatch.delenv("S3_RAW_BUCKET", raising=False)
    monkeypatch.delenv("METRICS_ENABLED", raising=False)

    result = process_file(input_path, tmp_path / "processed.json")

    assert result["dynamodb_written"] == 2
    assert result["alerts_published"] == 1
    assert len(captured["dynamodb_transactions"]) == 2
    assert captured["dynamodb_settings"].dynamodb_table == "table-test"
    assert captured["processed_at"].endswith("Z")
    assert len(captured["sns_transactions"]) == 2
    assert captured["sns_settings"].sns_topic_arn == "arn:aws:sns:us-east-2:123:topic-test"
