import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.risk.features import (
    FEATURE_NAMES,
    NO_HISTORY_SECONDS_SINCE_LAST,
    build_features,
    build_features_batch,
)


def _row(ts, amount, mcc, merchant):
    return {"ts": ts, "amount_cents": amount, "mcc": mcc, "merchant_name": merchant}


def test_no_leakage_future_rows_do_not_change_features():
    base = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    history = [
        _row(base - timedelta(hours=10), 10_000, "5411", "GROCERY A"),
        _row(base - timedelta(hours=3), 12_000, "5411", "GROCERY A"),
        _row(base - timedelta(hours=1), 9_000, "5812", "CAFE B"),
    ]
    tx = {"ts": base, "amount_cents": 11_000, "mcc": "5411", "merchant_name": "GROCERY A"}
    before = build_features(tx, history)

    future = [
        _row(base, 99_999_999, "7997", "CASINO X"),
        _row(base + timedelta(minutes=1), 99_999_999, "7997", "CASINO X"),
        _row(base + timedelta(days=1), 50_000_000, "5411", "GROCERY A"),
    ]
    after = build_features(tx, history + future)
    assert after == before

    assert before["count_24h"] == 3
    assert before["count_1h"] == 1
    assert before["seconds_since_last"] == pytest.approx(3600.0)
    assert before["merchant_new_to_card"] == 0.0
    assert before["mcc_share_30d"] == pytest.approx(2 / 3)
    assert before["amount_zscore_30d"] != 0.0


def test_empty_history_defaults():
    tx = {
        "ts": datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc),
        "amount_cents": 5_000,
        "mcc": "5411",
        "merchant_name": "NEW SHOP",
    }
    features = build_features(tx, [])
    assert features["seconds_since_last"] == NO_HISTORY_SECONDS_SINCE_LAST
    assert features["amount_zscore_30d"] == 0.0
    assert features["mcc_share_30d"] == 0.0
    assert features["count_1h"] == 0.0
    assert features["count_24h"] == 0.0
    assert features["merchant_new_to_card"] == 1.0
    assert features["amount_cents"] == 5_000.0
    expected_sin = math.sin(2 * math.pi * 13.5 / 24.0)
    assert features["hour_sin"] == pytest.approx(expected_sin, abs=1e-9)


def test_batch_matches_single_builder():
    rng = np.random.default_rng(7)
    base = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = []
    for card in ["card_a", "card_b", "card_c"]:
        t = base
        for _ in range(25):
            t = t + timedelta(hours=float(rng.uniform(0.2, 8)))
            rows.append(
                {
                    "card_id": card,
                    "ts": t,
                    "amount_cents": int(rng.lognormal(9, 0.6)),
                    "mcc": str(rng.choice(["5411", "5812", "5541", "3000"])),
                    "merchant_name": str(rng.choice(["M1", "M2", "M3", "M4"])),
                }
            )
    df = pd.DataFrame(rows)
    batch = build_features_batch(df)
    assert list(batch.columns) == FEATURE_NAMES

    for card in ["card_a", "card_b", "card_c"]:
        card_df = df[df.card_id == card].sort_values("ts")
        for i in (0, 1, 5, 24):
            row = card_df.iloc[i]
            original_index = card_df.index[i]
            tx = {
                "ts": row["ts"],
                "amount_cents": int(row["amount_cents"]),
                "mcc": str(row["mcc"]),
                "merchant_name": str(row["merchant_name"]),
            }
            history = card_df.iloc[:i].to_dict("records")
            single = build_features(tx, history)
            for name in FEATURE_NAMES:
                assert batch.loc[original_index, name] == pytest.approx(
                    single[name], abs=1e-9
                ), f"mismatch for {card} row {i} feature {name}"
