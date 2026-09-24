import os


class Config:
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://cardguard:cardguard@localhost:5432/cardguard",
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


class ProductionConfig(Config):
    pass
