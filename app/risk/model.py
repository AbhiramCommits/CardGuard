import logging
import os

import joblib
import numpy as np

from app.risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.risk.policy import ReasonCode

logger = logging.getLogger("cardguard")

DEFAULT_MODEL_PATH = "ml/artifacts/model_v1.joblib"


def bucket(proba, threshold_low, threshold_high):
    if proba < threshold_low:
        return "approve"
    if proba >= threshold_high:
        return "decline"
    return "review"


class RiskModel:
    def __init__(self, path=None):
        self.path = path or os.environ.get("CARDGUARD_MODEL_PATH", DEFAULT_MODEL_PATH)
        self._model = None
        self.threshold_low = None
        self.threshold_high = None
        self.schema_version = None

    @property
    def available(self):
        return self._model is not None

    def load(self, path=None):
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
            logger.error(
                "failed to load risk model from %s; policy-only decisioning",
                self.path,
                exc_info=True,
            )

    def predict_proba(self, vectors):
        model = self._model
        if model is None:
            return None
        if hasattr(model, "predict_proba"):
            proba = np.asarray(model.predict_proba(vectors))
            return proba[:, 1] if proba.ndim == 2 else proba
        raw = np.asarray(model.predict(vectors), dtype=float)
        return 1.0 / (1.0 + np.exp(-raw))

    def score(self, features):
        if self._model is None:
            return None
        try:
            vector = np.array([[float(features[name]) for name in FEATURE_NAMES]])
            proba = self.predict_proba(vector)
            if proba is None:
                return None
            return float(proba[0])
        except Exception:
            logger.error("risk model scoring failed", exc_info=True)
            return None

    def decide(self, features):
        proba = self.score(features)
        if proba is None:
            return None
        decision = bucket(proba, self.threshold_low, self.threshold_high)
        if decision == "decline":
            return decision, ReasonCode.MODEL_HIGH_RISK.value, proba
        if decision == "review":
            return decision, ReasonCode.MODEL_REVIEW.value, proba
        return decision, None, proba


risk_model = RiskModel()
