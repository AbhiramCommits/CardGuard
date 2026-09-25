import logging
import os
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np

from app.metrics import MODEL_SCORE
from app.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.risk.policy import ReasonCode

logger = logging.getLogger("cardguard")

DEFAULT_MODEL_PATH = "ml/artifacts/model_v1.joblib"


def bucket(proba: float, threshold_low: float, threshold_high: float) -> str:
    if proba < threshold_low:
        return "approve"
    if proba >= threshold_high:
        return "decline"
    return "review"


class RiskModel:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("CARDGUARD_MODEL_PATH", DEFAULT_MODEL_PATH)
        self._model: Any = None
        self.threshold_low: float | None = None
        self.threshold_high: float | None = None
        self.schema_version: str | None = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self, path: str | None = None) -> None:
        if path is not None:
            self.path = path
        try:
            artifact = joblib.load(self.path)
            schema = artifact.get("schema_version")
            names = artifact.get("feature_names")
            if schema != FEATURE_SCHEMA_VERSION or names != FEATURE_NAMES:
                raise ValueError(
                    f"artifact schema mismatch: expected {FEATURE_SCHEMA_VERSION} "
                    f"with features {FEATURE_NAMES}, got {schema} with {names}"
                )
            self._model = artifact["model"]
            self.threshold_low = float(artifact["threshold_low"])
            self.threshold_high = float(artifact["threshold_high"])
            self.schema_version = schema
            logger.info("risk model loaded from %s (schema %s)", self.path, schema)
        except FileNotFoundError:
            self._model = None
            self.threshold_low = None
            self.threshold_high = None
            self.schema_version = None
            logger.error(
                "risk model file missing at %s; policy-only decisioning", self.path
            )
        except Exception:
            self._model = None
            self.threshold_low = None
            self.threshold_high = None
            self.schema_version = None
            logger.exception(
                "failed to load risk model from %s; policy-only decisioning", self.path
            )

    def predict_proba(self, vectors: np.ndarray) -> np.ndarray | None:
        model = self._model
        if model is None:
            return None
        if hasattr(model, "predict_proba"):
            proba = np.asarray(model.predict_proba(vectors))
            return proba[:, 1] if proba.ndim == 2 else proba
        raw = np.asarray(model.predict(vectors), dtype=float)
        return 1.0 / (1.0 + np.exp(-raw))

    def score(self, features: dict[str, float]) -> float | None:
        if self._model is None:
            return None
        try:
            vector = np.array([[float(features[name]) for name in FEATURE_NAMES]])
            proba = self.predict_proba(vector)
            if proba is None:
                return None
            value = float(proba[0])
            MODEL_SCORE.observe(value)
            return value
        except Exception:
            logger.exception("risk model scoring failed")
            return None

    def decide(
        self, features: dict[str, float]
    ) -> tuple[str, str | None, float] | None:
        proba = self.score(features)
        if proba is None:
            return None
        assert self.threshold_low is not None and self.threshold_high is not None
        decision = bucket(proba, self.threshold_low, self.threshold_high)
        if decision == "decline":
            return decision, ReasonCode.MODEL_HIGH_RISK.value, proba
        if decision == "review":
            return decision, ReasonCode.MODEL_REVIEW.value, proba
        return decision, None, proba


risk_model = RiskModel()
