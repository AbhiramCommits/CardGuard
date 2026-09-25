import os
from typing import ClassVar


class Config:
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://cardguard:cardguard@localhost:5432/cardguard",
    )
    MODEL_PATH = os.environ.get("CARDGUARD_MODEL_PATH", "ml/artifacts/model_v1.joblib")
    REVIEW_TIMEOUT_SECONDS = int(
        os.environ.get("CARDGUARD_REVIEW_TIMEOUT_SECONDS", "86400")
    )
    API_KEYS: ClassVar[frozenset[str]] = frozenset(
        key.strip()
        for key in os.environ.get("CARDGUARD_API_KEYS", "dev-key").split(",")
        if key.strip()
    )
    RATE_LIMIT_PER_KEY = int(os.environ.get("CARDGUARD_RATE_LIMIT_PER_KEY", "600"))
    RATE_LIMIT_WINDOW_SECONDS = int(
        os.environ.get("CARDGUARD_RATE_LIMIT_WINDOW_SECONDS", "60")
    )
    MAX_CONTENT_LENGTH = 16 * 1024
    TESTING = False
    DEBUG = False


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    DATABASE_URL = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://cardguard:cardguard@localhost:5432/cardguard_test",
    )
    MODEL_PATH = os.environ.get(
        "CARDGUARD_TEST_MODEL_PATH", "/nonexistent/cardguard-test-model.joblib"
    )
    API_KEYS: ClassVar[frozenset[str]] = frozenset({"test-key"})
    RATE_LIMIT_PER_KEY = 0


class ProductionConfig(Config):
    pass
