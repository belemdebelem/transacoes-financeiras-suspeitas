"""Tests for the DynamoDB persistence helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.aws import dynamodb
from src.aws.settings import AwsSettings


class FakeBatchWriter:
    def __init__(self, table: "FakeTable") -> None:
        self.table = table

    def __enter__(self) -> "FakeBatchWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def put_item(self, Item):
        if self.table.fail_on_id == Item.get("transaction_id"):
            raise RuntimeError("falha simulada no batch")
        self.table.items.append(Item)


class FakeTable:
    def __init__(self) -> None:
        self.name: str | None = None
        self.items: list[dict] = []
        self.overwrite_by_pkeys = None
        self.fail_on_id: str | None = None
        self.enter_error: Exception | None = None

    def batch_writer(self, overwrite_by_pkeys=None):
        self.overwrite_by_pkeys = overwrite_by_pkeys
        if self.enter_error is not None:
            raise self.enter_error
        return FakeBatchWriter(self)


class FakeDynamoDBResource:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def Table(self, name: str) -> FakeTable:
        self.table.name = name
        return self.table


class FakeBoto3:
    def __init__(self, table: FakeTable) -> None:
        self.table = table
        self.service: str | None = None
        self.region: str | None = None

    def resource(self, service: str, region_name: str | None = None):
        self.service = service
        self.region = region_name
        return FakeDynamoDBResource(self.table)


def _transaction(**overrides):
    transaction = {
        "transaction_id": "txn_0001",
        "timestamp": "2026-10-05T12:00:00Z",
        "account_id": "ACC_0001",
        "customer_id": "CUST_0001",
        "transaction_type": "DEBIT",
        "amount": 1500.75,
        "currency": "BRL",
        "merchant_or_counterparty": "Loja A",
        "location": "SÃO PAULO",
        "channel": "APP",
        "risk_score": 50,
        "risk_level": "SUSPECT",
        "suspicious_reason": "HIGH_AMOUNT",
        "alert": True,
    }
    transaction.update(overrides)
    return transaction


def test_build_item_converts_values_for_dynamodb():
    item = dynamodb._build_item(
        _transaction(),
        processed_at="2026-10-05T13:00:00Z",
    )

    assert item["transaction_id"] == "txn_0001"
    assert item["event_date"] == "2026-10-05"
    assert item["amount"] == Decimal("1500.75")
    assert item["risk_score"] == 50
    assert item["alert"] is True
    assert item["processed_at"] == "2026-10-05T13:00:00Z"


def test_build_item_omits_missing_optional_fields():
    item = dynamodb._build_item({"transaction_id": "txn_0001"})

    assert item == {"transaction_id": "txn_0001"}


def test_build_item_requires_a_transaction_id():
    with pytest.raises(ValueError, match="transaction_id"):
        dynamodb._build_item({"amount": 10.0})


def test_from_decimal_restores_json_friendly_values():
    value = {
        "amount": Decimal("10.50"),
        "count": Decimal("2"),
        "nested": [Decimal("1.25")],
    }

    assert dynamodb._from_decimal(value) == {
        "amount": 10.5,
        "count": 2,
        "nested": [1.25],
    }


def test_put_processed_transactions_writes_the_batch(monkeypatch):
    table = FakeTable()
    fake_boto3 = FakeBoto3(table)
    monkeypatch.setattr(dynamodb, "_boto3", lambda: fake_boto3)
    settings = AwsSettings(region="sa-east-1", dynamodb_table="table-test")

    written = dynamodb.put_processed_transactions(
        [_transaction(), _transaction(transaction_id="txn_0002")],
        settings=settings,
        processed_at="2026-10-05T13:00:00Z",
    )

    assert written == 2
    assert fake_boto3.service == "dynamodb"
    assert fake_boto3.region == "sa-east-1"
    assert table.name == "table-test"
    assert table.overwrite_by_pkeys == ["transaction_id"]
    assert [item["transaction_id"] for item in table.items] == ["txn_0001", "txn_0002"]


def test_put_processed_transactions_skips_items_without_id(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(dynamodb, "_boto3", lambda: FakeBoto3(table))
    settings = AwsSettings(dynamodb_table="table-test")

    written = dynamodb.put_processed_transactions(
        [_transaction(), {"amount": 10.0}],
        settings=settings,
    )

    assert written == 1
    assert table.items[0]["transaction_id"] == "txn_0001"


def test_put_processed_transactions_continues_after_a_write_error(monkeypatch):
    table = FakeTable()
    table.fail_on_id = "txn_0001"
    monkeypatch.setattr(dynamodb, "_boto3", lambda: FakeBoto3(table))
    settings = AwsSettings(dynamodb_table="table-test")

    written = dynamodb.put_processed_transactions(
        [_transaction(), _transaction(transaction_id="txn_0002")],
        settings=settings,
    )

    assert written == 1
    assert table.items[0]["transaction_id"] == "txn_0002"


def test_put_processed_transactions_returns_zero_without_a_table(monkeypatch):
    def unexpected_boto3():
        raise AssertionError("boto3 não deveria ser chamado")

    monkeypatch.setattr(dynamodb, "_boto3", unexpected_boto3)

    assert dynamodb.put_processed_transactions([_transaction()], settings=AwsSettings()) == 0


def test_put_processed_transactions_returns_zero_for_an_empty_batch(monkeypatch):
    def unexpected_boto3():
        raise AssertionError("boto3 não deveria ser chamado")

    monkeypatch.setattr(dynamodb, "_boto3", unexpected_boto3)
    settings = AwsSettings(dynamodb_table="table-test")

    assert dynamodb.put_processed_transactions([], settings=settings) == 0


def test_put_processed_transactions_handles_a_batch_writer_failure(monkeypatch):
    table = FakeTable()
    table.enter_error = RuntimeError("tabela indisponível")
    monkeypatch.setattr(dynamodb, "_boto3", lambda: FakeBoto3(table))
    settings = AwsSettings(dynamodb_table="table-test")

    written = dynamodb.put_processed_transactions([_transaction()], settings=settings)

    assert written == 0


def test_put_processed_transactions_accepts_an_explicit_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(dynamodb, "_boto3", lambda: FakeBoto3(table))

    written = dynamodb.put_processed_transactions(
        [_transaction()],
        table_name="explicit-table",
        settings=AwsSettings(),
    )

    assert written == 1
    assert table.name == "explicit-table"


def test_put_processed_transaction_reports_success(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(dynamodb, "_boto3", lambda: FakeBoto3(table))
    settings = AwsSettings(dynamodb_table="table-test")

    assert dynamodb.put_processed_transaction(_transaction(), settings=settings) is True
    assert len(table.items) == 1


def test_put_processed_transaction_reports_failure_without_a_table():
    assert dynamodb.put_processed_transaction(_transaction(), settings=AwsSettings()) is False
