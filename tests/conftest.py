import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import TestingConfig
from app.models import (
    Account,
    AccountType,
    Authorization,
    Base,
    Card,
    Company,
    Employee,
    SpendPolicy,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", TestingConfig.DATABASE_URL)


def _create_database_if_missing(url):
    target = make_url(url)
    admin = target.set(database="postgres")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def engine():
    _create_database_if_missing(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    with engine.begin() as conn:
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def company(session):
    company = Company(name="TestCo")
    session.add(company)
    session.flush()
    for account_type in AccountType:
        session.add(Account(company_id=company.id, account_type=account_type))
    session.commit()
    return company


@pytest.fixture()
def card(session, company):
    employee = Employee(
        company_id=company.id, name="Alice", email="alice@testco.example.com"
    )
    session.add(employee)
    session.flush()
    card = Card(employee_id=employee.id, last_four="4242")
    session.add(card)
    session.flush()
    session.add(
        SpendPolicy(
            employee_id=employee.id,
            monthly_limit_cents=1_000_000,
            per_transaction_limit_cents=100_000,
            blocked_mccs=[],
            velocity_max_auths=10,
            velocity_window_minutes=15,
        )
    )
    session.commit()
    return card


@pytest.fixture()
def authorization(session, card):
    authorization = Authorization(
        idempotency_key=f"authz-{uuid4().hex}",
        card_id=card.id,
        merchant_name="ACME GROCERIES",
        mcc="5411",
        amount_cents=10_000,
    )
    session.add(authorization)
    session.commit()
    return authorization


@pytest.fixture()
def app():
    from app import create_app

    return create_app("app.config.TestingConfig")
