import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_CARDS = 500
N_TXNS = 200_000
SPAN_DAYS = 180
BASE_TS = datetime(2025, 6, 1, tzinfo=UTC)
OUT_PATH = Path(__file__).resolve().parent / "data" / "transactions.csv"

MCC_POOL = [
    "3000",
    "4111",
    "4511",
    "4722",
    "4900",
    "5411",
    "5462",
    "5541",
    "5812",
    "5814",
    "5912",
    "5942",
    "5999",
    "7230",
    "7311",
    "7832",
    "7997",
    "8062",
    "8099",
    "8398",
]
MERCHANTS_PER_MCC = 60


def _gauss(x, mu, sigma):
    return math.exp(-((x - mu) ** 2) / (2 * sigma * sigma))


def _merchant_name(mcc, k):
    return f"{mcc}-SHOP-{k:02d}"


def _merchant_id(mcc, k):
    return f"m_{mcc}_{k:02d}"


def _weighted_choice(rng, items, weights):
    total = sum(weights)
    r = rng.random() * total
    for item, weight in zip(items, weights):
        r -= weight
        if r <= 0:
            return item
    return items[-1]


def _build_card_profiles(rng):
    profiles = {}
    for c in range(N_CARDS):
        card_id = f"card_{c:04d}"
        favorites = rng.sample(MCC_POOL, 5)
        favorite_weights = {mcc: rng.uniform(1.5, 4.0) for mcc in favorites}
        mcc_weights = {
            mcc: favorite_weights.get(mcc, rng.uniform(0.03, 0.25)) for mcc in MCC_POOL
        }
        amount_mean = rng.lognormvariate(math.log(9000), 0.6)
        peak1 = rng.uniform(10.0, 17.0)
        peak2 = rng.uniform(8.0, 20.0)
        hour_weights = [
            _gauss(h, peak1, 2.5) + 0.5 * _gauss(h, peak2, 3.5) + 0.02
            for h in range(24)
        ]
        merchant_pool = [
            (_merchant_name(mcc, k), _merchant_id(mcc, k))
            for mcc in MCC_POOL
            for k in range(MERCHANTS_PER_MCC)
        ]
        n_merchants = rng.randint(5, 12)
        merchant_set = []
        for _ in range(n_merchants):
            mcc = _weighted_choice(rng, MCC_POOL, list(mcc_weights.values()))
            merchant_set.append(
                rng.choice([m for m in merchant_pool if m[0].startswith(mcc)])
            )
        profiles[card_id] = {
            "favorites": set(favorites),
            "mcc_weights": mcc_weights,
            "amount_mean": amount_mean,
            "hour_weights": hour_weights,
            "merchant_set": set(merchant_set),
            "merchants_by_mcc": {
                mcc: [m for m in merchant_pool if m[0].startswith(mcc)]
                for mcc in MCC_POOL
            },
        }
    return profiles


def generate():
    rng = random.Random(SEED)
    np_rng = np.random.default_rng(SEED + 1)
    profiles = _build_card_profiles(rng)

    rows = []
    fraud_rows = []
    for card_id, profile in profiles.items():
        n_legit = int(np_rng.poisson(N_TXNS / N_CARDS))
        for _ in range(n_legit):
            ts = BASE_TS + timedelta(
                days=rng.uniform(0, SPAN_DAYS),
                hours=_weighted_choice(rng, range(24), profile["hour_weights"]),
                minutes=rng.uniform(0, 60),
                seconds=rng.uniform(0, 60),
            )
            amount = round(rng.lognormvariate(math.log(profile["amount_mean"]), 0.5))
            mcc = _weighted_choice(rng, MCC_POOL, list(profile["mcc_weights"].values()))
            card_merchants = [
                m for m in profile["merchant_set"] if m[0].startswith(mcc)
            ]
            if card_merchants and rng.random() < 0.85:
                name, mid = rng.choice(card_merchants)
            else:
                name, mid = rng.choice(profile["merchants_by_mcc"][mcc])
            rows.append(
                {
                    "card_id": card_id,
                    "ts": ts,
                    "amount_cents": max(amount, 100),
                    "mcc": mcc,
                    "merchant_id": mid,
                    "merchant_name": name,
                    "is_fraud": 0,
                }
            )

        n_fraud = int(np_rng.poisson(1.2))
        if not rows:
            continue
        for _ in range(n_fraud):
            anchor = None
            if rng.random() < 0.6:
                anchor = (
                    rng.choice(rows[-300:]) if len(rows) > 300 else rng.choice(rows)
                )
                ts = anchor["ts"] + timedelta(seconds=rng.uniform(30, 120))
            else:
                ts = BASE_TS + timedelta(
                    days=rng.uniform(0, SPAN_DAYS),
                    hours=rng.uniform(0, 5),
                    minutes=rng.uniform(0, 60),
                    seconds=rng.uniform(0, 60),
                )
            amount = round(profile["amount_mean"] * rng.uniform(4, 15))
            if rng.random() < 0.9:
                mcc = rng.choice([m for m in MCC_POOL if m not in profile["favorites"]])
            else:
                mcc = rng.choice(MCC_POOL)
            if rng.random() < 0.9:
                name, mid = rng.choice(profile["merchants_by_mcc"][mcc])
            else:
                name, mid = rng.choice(
                    [
                        m
                        for m in profile["merchants_by_mcc"][mcc]
                        if m not in profile["merchant_set"]
                    ]
                    or profile["merchants_by_mcc"][mcc]
                )
            fraud_rows.append(
                {
                    "card_id": card_id,
                    "ts": ts,
                    "amount_cents": max(amount, 100),
                    "mcc": mcc,
                    "merchant_id": mid,
                    "merchant_name": name,
                    "is_fraud": 1,
                }
            )

    df = pd.DataFrame(rows + fraud_rows)
    df = df.sort_values("ts").reset_index(drop=True)
    df.insert(0, "transaction_id", np.arange(len(df)))
    df["ts"] = df["ts"].map(lambda t: t.isoformat())

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    fraud_rate = df["is_fraud"].mean()
    print(
        f"generated {len(df)} transactions ({len(df['card_id'].unique())} cards) "
        f"with {int(df['is_fraud'].sum())} frauds ({fraud_rate:.2%}) -> {OUT_PATH}"
    )


if __name__ == "__main__":
    generate()
