"""Tests for the SNS alert publisher."""

from __future__ import annotations

from src.aws import sns_publisher
from src.aws.settings import AwsSettings


class FakeSNSClient:
    def __init__(
        self,
        error: Exception | None = None,
        fail_on_subject: str | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.error = error
        self.fail_on_subject = fail_on_subject

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.fail_on_subject and self.fail_on_subject in kwargs["Subject"]:
            raise RuntimeError("falha simulada no SNS")
        return {"MessageId": "fake-message-id"}


def _transaction(**overrides):
    transaction = {
        "transaction_id": "txn_0001",
        "timestamp": "2026-10-05T12:00:00Z",
        "account_id": "ACC_0001",
        "amount": 18500.0,
        "risk_score": 50,
        "risk_level": "SUSPECT",
        "suspicious_reason": "HIGH_AMOUNT",
        "alert": True,
    }
    transaction.update(overrides)
    return transaction


def _settings(**overrides):
    values = {
        "sns_topic_arn": "arn:aws:sns:us-east-2:123:topic-test",
        "alerts_enabled": True,
    }
    values.update(overrides)
    return AwsSettings(**values)


def test_format_alert_message_contains_the_review_fields():
    message = sns_publisher._format_alert_message(_transaction())

    assert "ALERTA - Transação Financeira Suspeita" in message
    assert "Transaction ID: txn_0001" in message
    assert "Account ID: ACC_0001" in message
    assert "Timestamp: 2026-10-05T12:00:00Z" in message
    assert "Amount: 18500.0" in message
    assert "Risk Level: SUSPECT" in message
    assert "Risk Score: 50" in message
    assert "Reason: HIGH_AMOUNT" in message


def test_publish_alert_sends_the_configured_topic():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        _transaction(), settings=_settings(), client=client
    )

    assert published is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["TopicArn"] == "arn:aws:sns:us-east-2:123:topic-test"
    assert call["Subject"] == "Alerta SUSPECT - txn_0001"
    assert "Transaction ID: txn_0001" in call["Message"]


def test_publish_alert_uses_an_explicit_topic():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        _transaction(),
        topic_arn="arn:aws:sns:us-east-2:123:other-topic",
        settings=_settings(),
        client=client,
    )

    assert published is True
    assert client.calls[0]["TopicArn"] == "arn:aws:sns:us-east-2:123:other-topic"


def test_publish_alert_truncates_the_subject():
    client = FakeSNSClient()
    transaction = _transaction(transaction_id="txn_" + "x" * 200)

    sns_publisher.publish_alert(transaction, settings=_settings(), client=client)

    assert len(client.calls[0]["Subject"]) == 100


def test_publish_alert_skips_without_a_topic():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        _transaction(), settings=AwsSettings(), client=client
    )

    assert published is False
    assert client.calls == []


def test_publish_alert_skips_when_alerts_are_disabled():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        _transaction(), settings=_settings(alerts_enabled=False), client=client
    )

    assert published is False
    assert client.calls == []


def test_publish_alert_allows_an_explicit_topic_when_disabled():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        _transaction(),
        topic_arn="arn:aws:sns:us-east-2:123:manual-topic",
        settings=_settings(alerts_enabled=False),
        client=client,
    )

    assert published is True
    assert len(client.calls) == 1


def test_publish_alert_rejects_a_non_dict_transaction():
    client = FakeSNSClient()

    published = sns_publisher.publish_alert(
        "not-a-transaction", settings=_settings(), client=client
    )

    assert published is False
    assert client.calls == []


def test_publish_alert_does_not_break_on_client_errors():
    client = FakeSNSClient(error=RuntimeError("sns indisponível"))

    published = sns_publisher.publish_alert(
        _transaction(), settings=_settings(), client=client
    )

    assert published is False


def test_publish_alerts_only_sends_alerted_transactions():
    client = FakeSNSClient()
    transactions = [
        _transaction(),
        _transaction(transaction_id="txn_0002", alert=False),
        _transaction(transaction_id="txn_0003", alert=True),
        "not-a-transaction",
    ]

    published = sns_publisher.publish_alerts(
        transactions, settings=_settings(), client=client
    )

    assert published == 2
    assert [call["Subject"] for call in client.calls] == [
        "Alerta SUSPECT - txn_0001",
        "Alerta SUSPECT - txn_0003",
    ]


def test_publish_alerts_returns_zero_for_empty_input():
    client = FakeSNSClient()

    assert sns_publisher.publish_alerts([], settings=_settings(), client=client) == 0
    assert sns_publisher.publish_alerts({}, settings=_settings(), client=client) == 0
    assert client.calls == []


def test_publish_alerts_counts_only_successful_publications():
    client = FakeSNSClient(fail_on_subject="txn_0002")
    transactions = [
        _transaction(),
        _transaction(transaction_id="txn_0002"),
        _transaction(transaction_id="txn_0003"),
    ]

    published = sns_publisher.publish_alerts(
        transactions, settings=_settings(), client=client
    )

    assert published == 2
    assert len(client.calls) == 3
