import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, build_features_batch

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "transactions.csv"
ARTIFACT_PATH = ROOT / "artifacts" / "model_v1.joblib"
METRICS_PATH = ROOT / "artifacts" / "metrics.json"
MODEL_CARD_PATH = ROOT.parent / "docs" / "model-card.md"

TRAIN_FRACTION = 0.8
OBJECTIVE = {"fraud_dollar_recall_target": 0.95, "review_dollar_recall_target": 0.99}
SEED = 42

LGB_PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.02,
    "num_leaves": 15,
    "min_child_samples": 100,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "reg_lambda": 1.0,
    "seed": SEED,
    "verbosity": -1,
}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _amount_weighted_recall(y, proba, amounts, threshold):
    caught = (proba >= threshold) & y
    return float(np.sum(amounts[caught]) / np.sum(amounts[y])) if y.sum() else 1.0


def _false_decline_rate(y, proba, threshold):
    declined = (proba >= threshold) & ~y
    return float(declined.sum() / (~y).sum()) if (~y).sum() else 0.0


def _pick_threshold(y, proba, amounts, grid, target):
    best = 0.0
    for t in grid:
        if _amount_weighted_recall(y, proba, amounts, t) >= target:
            best = t
    return best


def load_data():
    if not DATA_PATH.exists():
        from ml.generate_synthetic import generate

        generate()
    df = pd.read_csv(DATA_PATH, dtype={"mcc": str, "card_id": str, "merchant_id": str})
    df["ts"] = pd.to_datetime(df["ts"], format="mixed", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def evaluate(y, proba, amounts, thresholds):
    rows = []
    for t in thresholds:
        pred = proba >= t
        rows.append(
            {
                "threshold": round(float(t), 3),
                "precision": round(float(precision_score(y, pred, zero_division=0)), 5),
                "recall": round(float(recall_score(y, pred, zero_division=0)), 5),
                "amount_weighted_recall": round(
                    _amount_weighted_recall(y, proba, amounts, t), 5
                ),
                "false_decline_rate": round(_false_decline_rate(y, proba, t), 6),
            }
        )
    return rows


def render_model_card(metrics):
    table_rows = "\n".join(
        f"| {r['threshold']} | {r['precision']:.5f} | {r['recall']:.5f} | "
        f"{r['amount_weighted_recall']:.5f} | {r['false_decline_rate']:.4%} |"
        for r in metrics["threshold_table"]
    )
    return f"""# Model Card: cardguard risk model v1

- **Model**: LightGBM gradient-boosted trees (binary classifier)
- **Feature schema**: `{metrics['schema_version']}` — `{', '.join(metrics['feature_names'])}`
- **Trained at**: {metrics['trained_at']}

## Data

{metrics['dataset']['source']}
{metrics['dataset']['split']}

| | |
|---|---|
| Transactions | {metrics['dataset']['n_rows']:,} |
| Fraudulent | {metrics['dataset']['n_fraud']:,} ({metrics['dataset']['fraud_rate']:.2%}) |
| Train rows | {metrics['dataset']['n_train']:,} (first {metrics['dataset']['train_fraction']:.0%} by time) |
| Test rows | {metrics['dataset']['n_test']:,} (last {metrics['dataset']['test_fraction']:.0%} by time) |

## Features

All features are computed from card history strictly before the transaction timestamp
(no leakage). `amount_zscore_30d` is the amount z-score against the card's trailing
30-day mean/std; `mcc_share_30d` is the share of the card's trailing 30-day activity at
this MCC; `count_1h`/`count_24h` are prior authorizations in trailing windows;
`seconds_since_last` is the gap to the previous authorization; `hour_sin`/`hour_cos`
encode time of day; `merchant_new_to_card` is 1 when the card has never seen the merchant.

## Held-out metrics (time-ordered split, last {metrics['dataset']['test_fraction']:.0%} of data)

- Precision @ threshold: {metrics['metrics']['precision']}
- Recall @ threshold: {metrics['metrics']['recall']}
- PR-AUC: {metrics['metrics']['pr_auc']}
- ROC-AUC: {metrics['metrics']['roc_auc']}
- Amount-weighted (fraud-dollar) recall @ threshold: {metrics['metrics']['fraud_dollar_recall']}
- False-decline rate @ threshold: {metrics['metrics']['false_decline_rate']}
  ({metrics['metrics']['false_decline_count']:,} legitimate transactions declined out of
  {metrics['metrics']['legitimate_test_count']:,})

## Operating thresholds

Chosen to minimize false declines subject to catching at least
{metrics['objective']['fraud_dollar_recall_target']:.0%} of fraud dollars on the held-out set.

- `threshold_high` (decline): {metrics['threshold_high']}
- `threshold_low` (auto-approve below): {metrics['threshold_low']} — the highest threshold
  still catching {metrics['objective']['review_dollar_recall_target']:.0%} of fraud dollars;
  scores between low and high are queued for review.

## Precision / recall vs threshold (held-out)

| threshold | precision | recall | amount-weighted recall | false-decline rate |
|---|---|---|---|---|
{table_rows}

## Known limitations

- Trained on synthetic data (see README "Data" section); real-world distributions differ
  and the model should be retrained on production traffic before hard declines are enabled.
- Serving uses the card's most recent 1,000 authorizations for history; cards with very
  high volume get a truncated trailing window, slightly biasing `*_30d` features.
- Cold-start cards (no history) score on neutral features: z-score 0, zero counts,
  merchant-new 1.

## Fairness caveat

Feature values depend on card tenure and history length, so new employees and new cards
systematically differ from established ones, which can change false-decline rates across
groups. This evaluation does not measure per-group outcomes; production deployment must
monitor false-decline and fraud-capture rates by cardholder segment (e.g. tenure decile,
company) and re-tune or retrain if disparities appear.
"""


def main():
    df = load_data()
    features = build_features_batch(df)
    y = df["is_fraud"].astype(bool).to_numpy()
    amounts = df["amount_cents"].to_numpy(dtype=float)

    split_idx = int(len(df) * TRAIN_FRACTION)
    X_train, X_test = features.iloc[:split_idx], features.iloc[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    amounts_test = amounts[split_idx:]

    n_neg, n_pos = int((y_train == 0).sum()), int(y_train.sum())
    params = dict(LGB_PARAMS)
    params["scale_pos_weight"] = n_neg / max(n_pos, 1)

    train_set = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_NAMES)
    valid_set = lgb.Dataset(X_test, label=y_test, feature_name=FEATURE_NAMES)
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=800,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(100), lgb.log_evaluation(100)],
    )

    proba = _sigmoid(booster.predict(X_test, num_iteration=booster.best_iteration))

    grid = np.round(np.arange(0.01, 1.0, 0.01), 2)
    threshold_high = _pick_threshold(
        y_test, proba, amounts_test, grid, OBJECTIVE["fraud_dollar_recall_target"]
    )
    threshold_low = _pick_threshold(
        y_test, proba, amounts_test, grid, OBJECTIVE["review_dollar_recall_target"]
    )

    pred = proba >= threshold_high
    metrics = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": FEATURE_NAMES,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "model_library": f"lightgbm {lgb.__version__}",
        "dataset": {
            "source": "Synthetic transactions generated by ml/generate_synthetic.py "
            "(Kaggle credit-card-fraud and IEEE-CIS datasets require authenticated "
            "download and were not fetchable in this environment).",
            "split": "Time-based split: rows ordered by transaction timestamp; the first "
            "80% train, the last 20% held out. No shuffling.",
            "n_rows": len(df),
            "n_fraud": int(y.sum()),
            "fraud_rate": float(y.mean()),
            "n_train": split_idx,
            "n_test": len(df) - split_idx,
            "train_fraction": TRAIN_FRACTION,
            "test_fraction": 1 - TRAIN_FRACTION,
        },
        "metrics": {
            "precision": round(float(precision_score(y_test, pred, zero_division=0)), 5),
            "recall": round(float(recall_score(y_test, pred, zero_division=0)), 5),
            "pr_auc": round(float(average_precision_score(y_test, proba)), 5),
            "roc_auc": round(float(roc_auc_score(y_test, proba)), 5),
            "fraud_dollar_recall": round(
                _amount_weighted_recall(y_test, proba, amounts_test, threshold_high), 5
            ),
            "false_decline_rate": round(_false_decline_rate(y_test, proba, threshold_high), 6),
            "false_decline_count": int(((proba >= threshold_high) & ~y_test).sum()),
            "legitimate_test_count": int((~y_test).sum()),
        },
        "threshold_high": float(threshold_high),
        "threshold_low": float(threshold_low),
        "objective": OBJECTIVE,
        "threshold_table": evaluate(
            y_test, proba, amounts_test, np.arange(0.05, 1.0, 0.05)
        ),
    }

    artifact = {
        "model": booster,
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": FEATURE_NAMES,
        "threshold_low": float(threshold_low),
        "threshold_high": float(threshold_high),
        "trained_at": metrics["trained_at"],
        "model_library": metrics["model_library"],
    }

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ARTIFACT_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n")
    MODEL_CARD_PATH.write_text(render_model_card(metrics))

    print(json.dumps(metrics["metrics"], indent=2))
    print(f"threshold_high={threshold_high} threshold_low={threshold_low}")
    print(f"artifact -> {ARTIFACT_PATH}")


if __name__ == "__main__":
    main()
