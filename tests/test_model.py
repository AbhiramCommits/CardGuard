import time

import joblib
import numpy as np
import pytest
from sqlalchemy import select

from app.config import TestingConfig
from app.models import Authorization, AuthorizationStatus
from app.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.risk.model import RiskModel, bucket, risk_model

from tests.test_authorization_api import _payload, _post, _postings


class _StubModel:
    def __init__(self, proba):
        self.proba = float(proba)

    def predict_proba(self, vectors):
        n = len(vectors)
        return np.column_stack([np.full(n, 1.0 - self.proba), np.full(n, self.proba)])


def _artifact(tmp_path, proba=0.5, low=0.3, high=0.7):
    path = tmp_path / "model.joblib"
    joblib.dump(
        {
            "model": _StubModel(proba),
            "schema_version": FEATURE_SCHEMA_VERSION,
            "feature_names": FEATURE_NAMES,
            "threshold_low": low,
            "threshold_high": high,
        },
        path,
    )
    return path


@pytest.fixture(autouse=True)
def _reset_model_after():
    yield
    risk_model.load(TestingConfig.MODEL_PATH)


def _features():
    return {name: 0.0 for name in FEATURE_NAMES}


def test_bucket_boundaries():
    assert bucket(0.0, 0.3, 0.7) == "approve"
    assert bucket(0.299999, 0.3, 0.7) == "approve"
    assert bucket(0.3, 0.3, 0.7) == "review"
    assert bucket(0.699999, 0.3, 0.7) == "review"
    assert bucket(0.7, 0.3, 0.7) == "decline"
    assert bucket(1.0, 0.3, 0.7) == "decline"


def test_decision_reasons_per_bucket(tmp_path):
    model = RiskModel(str(_artifact(tmp_path, proba=0.1)))
    model.load()
    assert model.decide(_features()) == ("approve", None, pytest.approx(0.1))

    model = RiskModel(str(_artifact(tmp_path, proba=0.5)))
    model.load()
    assert model.decide(_features()) == ("review", "MODEL_REVIEW", pytest.approx(0.5))

    model = RiskModel(str(_artifact(tmp_path, proba=0.9)))
    model.load()
    assert model.decide(_features()) == ("decline", "MODEL_HIGH_RISK", pytest.approx(0.9))


def test_missing_model_falls_back():
    model = RiskModel("/nonexistent/cardguard-test-model.joblib")
    model.load()
    assert model.available is False
    assert model.score(_features()) is None
    assert model.decide(_features()) is None


def test_schema_mismatch_unloads_model(tmp_path):
    path = tmp_path / "bad.joblib"
    joblib.dump(
        {
            "model": _StubModel(0.9),
            "schema_version": "v0",
            "feature_names": FEATURE_NAMES,
            "threshold_low": 0.3,
            "threshold_high": 0.7,
        },
        path,
    )
    model = RiskModel(str(path))
    model.load()
    assert model.available is False


def test_scoring_latency_p99_under_10ms(tmp_path):
    model = RiskModel(str(_artifact(tmp_path, proba=0.5)))
    model.load()
    assert model.available

    latencies = []
    for _ in range(1000):
        start = time.perf_counter()
        assert model.score(_features()) == pytest.approx(0.5)
        latencies.append((time.perf_counter() - start) * 1000)
    p99 = float(np.percentile(latencies, 99))
    assert p99 < 10.0


def test_api_model_decline_integration(app, session, card, tmp_path):
    risk_model.load(str(_artifact(tmp_path, proba=0.95)))
    payload = _payload(card)
    body = _post(app.test_client(), payload).get_json()

    assert body["decision"] == "decline"
    assert body["decision_reason"] == "MODEL_HIGH_RISK"
    assert body["risk_score"] == pytest.approx(95.0)

    authorization = session.scalar(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    )
    assert authorization.status == AuthorizationStatus.declined
    assert _postings(session, authorization.id) == []


def test_api_fallback_when_model_missing(app, session, card):
    payload = _payload(card)
    response = _post(app.test_client(), payload)
    body = response.get_json()
    assert response.status_code == 200
    assert body["decision"] == "approve"
    assert body["risk_score"] is None
