import os


class Config:
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://cardguard:cardguard@localhost:5432/cardguard",
    )
    MODEL_PATH = os.environ.get(
        "CARDGUARD_MODEL_PATH", "ml/artifacts/model_v1.joblib"
    )
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


class ProductionConfig(Config):
    pass
