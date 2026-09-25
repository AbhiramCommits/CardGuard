import math
from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

FEATURE_SCHEMA_VERSION = "v1"

FEATURE_NAMES = [
    "amount_cents",
    "amount_zscore_30d",
    "mcc_share_30d",
    "seconds_since_last",
    "count_1h",
    "count_24h",
    "hour_sin",
    "hour_cos",
    "merchant_new_to_card",
]

WINDOW_30D = 30 * 86400.0
WINDOW_1H = 3600.0
WINDOW_24H = 86400.0
NO_HISTORY_SECONDS_SINCE_LAST = 1_000_000.0


def _to_epoch(ts: datetime | str) -> float:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        else:
            ts = ts.astimezone(UTC)
        return ts.timestamp()
    parsed = datetime.fromisoformat(str(ts))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).timestamp()


def _hour_sin_cos(epoch: float) -> tuple[float, float]:
    utc = datetime.fromtimestamp(epoch, tz=UTC)
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    return math.sin(2 * math.pi * hour / 24.0), math.cos(2 * math.pi * hour / 24.0)


def _card_features(
    times: Sequence[float] | Any,
    amounts: Sequence[float] | Any,
    mccs: Sequence[str] | Any,
    merchants: Sequence[str] | Any,
) -> tuple[Any, ...]:
    import numpy as np

    n = len(times)
    times = np.asarray(times, dtype=float)
    amounts = np.asarray(amounts, dtype=float)
    mccs = [str(m) for m in mccs]
    merchants = [str(m) for m in merchants]

    zscore = np.zeros(n)
    mcc_share = np.zeros(n)
    seconds_since_last = np.full(n, NO_HISTORY_SECONDS_SINCE_LAST)
    count_1h = np.zeros(n)
    count_24h = np.zeros(n)
    hour_sin = np.zeros(n)
    hour_cos = np.zeros(n)
    merchant_new = np.ones(n)

    pref = np.zeros(n)
    pref2 = np.zeros(n)
    if n > 0:
        pref[0] = amounts[0]
        pref2[0] = amounts[0] * amounts[0]
        for i in range(1, n):
            pref[i] = pref[i - 1] + amounts[i]
            pref2[i] = pref2[i - 1] + amounts[i] * amounts[i]

    mcc_queues: dict[str, deque[int]] = defaultdict(deque)
    seen_merchants: set[str] = set()
    for i in range(n):
        t = times[i]
        hour_sin[i], hour_cos[i] = _hour_sin_cos(t)
        if i > 0:
            seconds_since_last[i] = t - times[i - 1]
            count_1h[i] = max(0, i - int(np.searchsorted(times, t - WINDOW_1H)))
            count_24h[i] = max(0, i - int(np.searchsorted(times, t - WINDOW_24H)))

            start = int(np.searchsorted(times, t - WINDOW_30D))
            count = i - start
            queue = mcc_queues[mccs[i]]
            while queue and queue[0] < start:
                queue.popleft()
            if count >= 2:
                total = pref[i - 1] - (pref[start - 1] if start > 0 else 0.0)
                total2 = pref2[i - 1] - (pref2[start - 1] if start > 0 else 0.0)
                mean = total / count
                var = total2 / count - mean * mean
                if var > 0:
                    zscore[i] = (amounts[i] - mean) / math.sqrt(var)
                mcc_share[i] = len(queue) / count
        merchant_new[i] = 0.0 if merchants[i] in seen_merchants else 1.0
        seen_merchants.add(merchants[i])
        mcc_queues[mccs[i]].append(i)

    return (
        amounts,
        zscore,
        mcc_share,
        seconds_since_last,
        count_1h,
        count_24h,
        hour_sin,
        hour_cos,
        merchant_new,
    )


def build_features(
    tx: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, float]:
    tx_ts = _to_epoch(tx["ts"])
    prior = [row for row in history if _to_epoch(row["ts"]) < tx_ts]
    prior.sort(key=lambda row: _to_epoch(row["ts"]))

    times = [_to_epoch(row["ts"]) for row in prior] + [tx_ts]
    amounts = [float(row["amount_cents"]) for row in prior] + [
        float(tx["amount_cents"])
    ]
    mccs = [str(row["mcc"]) for row in prior] + [str(tx["mcc"])]
    merchants = [str(row["merchant_name"]) for row in prior] + [
        str(tx["merchant_name"])
    ]

    values = _card_features(times, amounts, mccs, merchants)
    return {name: float(column[-1]) for name, column in zip(FEATURE_NAMES, values)}


def build_features_batch(df: Any) -> Any:
    import numpy as np
    import pandas as pd  # type: ignore[import-untyped]

    frame = df.sort_values(["card_id", "ts"])
    parts = []
    for _, group in frame.groupby("card_id", sort=False):
        g = group.sort_values("ts")
        times = np.array([_to_epoch(t) for t in g["ts"]], dtype=float)
        amounts = g["amount_cents"].to_numpy(dtype=float)
        mccs = g["mcc"].astype(str).tolist()
        merchants = g["merchant_name"].astype(str).tolist()
        values = _card_features(times, amounts, mccs, merchants)
        for name, column in zip(FEATURE_NAMES, values):
            g[name] = column
        parts.append(g)
    return pd.concat(parts).reindex(df.index)[FEATURE_NAMES]
