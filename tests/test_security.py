import logging
import re

import pytest
from sqlalchemy import select

from app.models import Authorization
from tests.test_authorization_api import API_HEADERS, _payload, _post

CARD_NUMBER_PATTERN = re.compile(r"\b\d{13,19}\b")


from typing import ClassVar


class _RateLimitedConfig:
    TESTING = True
    DATABASE_URL = (
        "postgresql+psycopg://cardguard:cardguard@localhost:5432/cardguard_test"
    )
    MODEL_PATH = "/nonexistent/cardguard-test-model.joblib"
    API_KEYS: ClassVar[frozenset[str]] = frozenset({"test-key"})
    RATE_LIMIT_PER_KEY = 3
    RATE_LIMIT_WINDOW_SECONDS = 60
    REVIEW_TIMEOUT_SECONDS = 86400
    MAX_CONTENT_LENGTH = 16 * 1024


@pytest.fixture()
def rate_limited_app():
    from app import create_app

    application = create_app(_RateLimitedConfig)
    yield application
    application.extensions["engine"].dispose()


def test_missing_api_key_returns_401(app, card):
    payload = _payload(card)
    response = app.test_client().post("/v1/authorizations", json=payload)
    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid or missing API key"


def test_wrong_api_key_returns_401(app, card):
    payload = _payload(card)
    response = app.test_client().post(
        "/v1/authorizations", json=payload, headers={"X-Api-Key": "wrong"}
    )
    assert response.status_code == 401


def test_valid_api_key_accepted(app, card):
    response = _post(app.test_client(), _payload(card))
    assert response.status_code == 200


def test_rate_limit_returns_429(rate_limited_app, card):
    client = rate_limited_app.test_client()
    for _ in range(3):
        response = _post(client, _payload(card))
        assert response.status_code == 200
    response = _post(client, _payload(card))
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_body_size_limit_returns_413(app, card):
    payload = _payload(card, merchant_name="X" * 20_000)
    response = app.test_client().post(
        "/v1/authorizations", json=payload, headers=API_HEADERS
    )
    assert response.status_code == 413


def test_metrics_endpoint_exposed(app):
    response = app.test_client().get("/metrics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "cardguard_auth_decisions_total" in body
    assert "cardguard_auth_latency_seconds" in body
    assert "cardguard_ledger_postings_total" in body
    assert "cardguard_model_score" in body


def test_never_logs_card_numbers(app, session, card, caplog):
    logger = logging.getLogger("cardguard")
    logger.propagate = True
    caplog.set_level(logging.INFO, logger="cardguard")

    response = _post(app.test_client(), _payload(card))
    assert response.status_code == 200

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert card.token not in logged, "card tokens must not appear in logs"
    assert not CARD_NUMBER_PATTERN.search(logged), (
        f"card-number-shaped value found in logs: {logged[:500]}"
    )

    authorizations = session.scalars(
        select(Authorization).where(Authorization.card_id == card.id)
    ).all()
    assert authorizations
    for authorization in authorizations:
        assert not CARD_NUMBER_PATTERN.search(authorization.merchant_name)
