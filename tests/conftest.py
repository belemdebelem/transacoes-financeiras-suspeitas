import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def valid_transaction():
    return {
        "transaction_id": "txn_000001",
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


@pytest.fixture
def repo_root():
    return REPO_ROOT
