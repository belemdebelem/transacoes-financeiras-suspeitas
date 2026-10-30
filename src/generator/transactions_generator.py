"""Synthetic financial transaction generation.

The generator is fully reproducible: the same ``seed`` and the same anchor
datetime always produce the same records. It also builds the patterns the
risk rules need to be meaningful:

* a stable profile per account (preferred channel, home location, usual amounts);
* accumulation bursts, so the short window rule has something to detect;
* anomalous transactions, so the high amount, unusual channel and unusual
  location rules are exercised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Iterable

DEFAULT_START_DATETIME = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_ACCOUNT_COUNT = 20
MIN_BASELINE_SAMPLES = 8
TRANSACTIONS_PER_ACCOUNT = 10
HISTORY_DAYS = 30
MINUTES_IN_DAY = 24 * 60
ANOMALY_EVERY = 5

TRANSACTION_TYPES = ("DEBIT", "CREDIT", "TRANSFER", "PAYMENT", "PIX")
CHANNELS = ("APP", "WEB", "ATM", "POS")
CHANNEL_WEIGHTS = (0.55, 0.25, 0.10, 0.10)

LOCATIONS = (
    "SÃO PAULO",
    "RIO DE JANEIRO",
    "BELO HORIZONTE",
    "CURITIBA",
    "BRASÍLIA",
    "PORTO ALEGRE",
    "BELÉM",
    "SALVADOR",
    "RECIFE",
)

MERCHANTS = (
    "Supermercado Central",
    "Loja Nova Era",
    "Farmácia Vida",
    "Pet Shop Rio",
    "Academia Fit",
    "Shop Fast",
    "Hotel Horizonte",
    "Prestação de Serviço",
    "Mercado Digital",
    "Aeroporto Viajante",
)


@dataclass(frozen=True)
class ClientProfile:
    """Behaviour profile used as the baseline of an account."""

    account_id: str
    customer_id: str
    primary_channel: str
    secondary_channel: str
    home_location: str
    secondary_location: str
    amount_low: float
    amount_high: float


def _build_profiles(rng: Random, account_count: int) -> list[ClientProfile]:
    profiles: list[ClientProfile] = []

    for index in range(1, account_count + 1):
        primary_channel = rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0]
        secondary_channel = rng.choice(
            [channel for channel in CHANNELS if channel != primary_channel]
        )
        home_location, secondary_location = rng.sample(LOCATIONS, 2)
        amount_low = rng.choice((15.0, 40.0, 80.0))
        amount_high = amount_low + rng.choice((900.0, 2500.0, 3400.0))

        profiles.append(
            ClientProfile(
                account_id=f"ACC_{index:04d}",
                customer_id=f"CUST_{index:04d}",
                primary_channel=primary_channel,
                secondary_channel=secondary_channel,
                home_location=home_location,
                secondary_location=secondary_location,
                amount_low=amount_low,
                amount_high=amount_high,
            )
        )

    return profiles


def _make_transaction(
    rng: Random,
    profile: ClientProfile,
    timestamp: datetime,
    transaction_id: int,
    channel: str,
    location: str,
    amount: float,
) -> dict[str, Any]:
    return {
        "transaction_id": f"txn_{transaction_id:06d}",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "account_id": profile.account_id,
        "customer_id": profile.customer_id,
        "transaction_type": rng.choice(TRANSACTION_TYPES),
        "amount": round(amount, 2),
        "currency": "BRL",
        "merchant_or_counterparty": rng.choice(MERCHANTS),
        "location": location,
        "channel": channel,
    }


def _random_timestamp(rng: Random, base_time: datetime) -> datetime:
    return base_time + timedelta(minutes=rng.randint(0, HISTORY_DAYS * MINUTES_IN_DAY))


def _unexpected_channel(rng: Random, profile: ClientProfile, prefer_atm: bool) -> str:
    """Return a channel the account does not use in its routine."""
    unexpected = [
        channel
        for channel in CHANNELS
        if channel not in (profile.primary_channel, profile.secondary_channel)
    ]
    if prefer_atm and "ATM" in unexpected:
        return "ATM"

    other_channels = [channel for channel in unexpected if channel != "ATM"]
    return rng.choice(other_channels or unexpected)


def generate_transactions(
    total_transactions: int = 250,
    seed: int = 42,
    start_datetime: datetime | None = None,
) -> list[dict[str, Any]]:
    """Generate a reproducible list of transactions."""
    rng = Random(seed)
    base_time = start_datetime or DEFAULT_START_DATETIME

    account_count = max(
        2, min(DEFAULT_ACCOUNT_COUNT, total_transactions // TRANSACTIONS_PER_ACCOUNT)
    )
    profiles = _build_profiles(rng, account_count)
    baseline_per_account = max(
        1, min(MIN_BASELINE_SAMPLES, total_transactions // account_count)
    )

    transactions: list[dict[str, Any]] = []
    index = 1

    # 1. Baseline history, so every account has a known behaviour pattern.
    for profile in profiles:
        for _ in range(baseline_per_account):
            channel = (
                profile.primary_channel
                if rng.random() < 0.92
                else profile.secondary_channel
            )
            location = (
                profile.home_location
                if rng.random() < 0.90
                else profile.secondary_location
            )
            amount = rng.uniform(profile.amount_low, profile.amount_high)
            transactions.append(
                _make_transaction(
                    rng,
                    profile,
                    _random_timestamp(rng, base_time),
                    index,
                    channel,
                    location,
                    amount,
                )
            )
            index += 1

    # 2. Accumulation bursts, inside a window shorter than 10 minutes.
    #    The burst keeps the usual channel and place, so it is only detected
    #    by the accumulation itself.
    burst_budget = max(0, (total_transactions - len(transactions)) // 20)
    used_accounts: set[str] = set()
    for _ in range(burst_budget):
        profile = rng.choice(profiles)
        if profile.account_id in used_accounts:
            continue
        used_accounts.add(profile.account_id)

        burst_size = rng.randint(3, 5)
        offsets = sorted(rng.sample(range(0, 540), burst_size))
        target = rng.uniform(9_000.0, 15_000.0)
        weights = [rng.uniform(0.7, 1.3) for _ in range(burst_size)]
        weight_sum = sum(weights)
        burst_start = _random_timestamp(rng, base_time)

        for offset, weight in zip(offsets, weights):
            transactions.append(
                _make_transaction(
                    rng,
                    profile,
                    burst_start + timedelta(seconds=offset),
                    index,
                    profile.primary_channel,
                    profile.home_location,
                    target * weight / weight_sum,
                )
            )
            index += 1

    # 3. Remaining volume: regular transactions, high amounts and anomalies.
    #    The round robin keeps every account with a comparable history, so the
    #    behaviour rules are not diluted by a single account.
    position = 0
    while len(transactions) < total_transactions:
        profile = profiles[position % len(profiles)]
        position += 1
        kind = rng.choices(
            ("normal", "elevated", "high", "suspicious"), weights=(6, 2, 2, 2)
        )[0]

        if kind == "normal":
            channel = (
                profile.primary_channel
                if rng.random() < 0.92
                else profile.secondary_channel
            )
            location = (
                profile.home_location
                if rng.random() < 0.90
                else profile.secondary_location
            )
            amount = rng.uniform(profile.amount_low, profile.amount_high)
        elif kind == "elevated":
            channel = _unexpected_channel(rng, profile, prefer_atm=position % 2 == 0)
            location = profile.home_location
            amount = rng.uniform(5_000.0, 9_900.0)
        elif kind == "high":
            channel = profile.primary_channel
            location = profile.home_location
            amount = rng.uniform(10_500.0, 40_000.0)
        else:
            channel = _unexpected_channel(rng, profile, prefer_atm=position % 2 == 0)
            location = profile.secondary_location
            amount = rng.uniform(12_000.0, 35_000.0)

        transactions.append(
            _make_transaction(
                rng,
                profile,
                _random_timestamp(rng, base_time),
                index,
                channel,
                location,
                amount,
            )
        )
        index += 1

    transactions = transactions[:total_transactions]
    transactions.sort(key=lambda item: (item["timestamp"], item["transaction_id"]))

    for position, transaction in enumerate(transactions, start=1):
        transaction["transaction_id"] = f"txn_{position:06d}"

    return transactions


def save_transactions(
    transactions: Iterable[dict[str, Any]], file_path: str | Path
) -> Path:
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(list(transactions), handle, ensure_ascii=False, indent=2)
    return target


def generate_batch_file(
    output_dir: str | Path | None = None,
    total_transactions: int = 250,
    seed: int = 42,
) -> Path:
    """Generate a batch file inside the raw data folder.

    The file name carries the ingestion timestamp, but its content is fully
    determined by ``seed`` and by the fixed anchor datetime.
    """
    base_dir = (
        Path(output_dir)
        if output_dir
        else Path(__file__).resolve().parents[2] / "data" / "raw"
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = base_dir / f"transactions_{timestamp}.json"
    transactions = generate_transactions(total_transactions=total_transactions, seed=seed)
    return save_transactions(transactions, output_path)


if __name__ == "__main__":
    generated_file = generate_batch_file()
    print(f"Arquivo gerado: {generated_file}")
