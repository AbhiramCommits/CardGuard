def test_healthz(app):
    response = app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_create_authorization_is_idempotent(app, card):
    client = app.test_client()
    payload = {
        "idempotency_key": "authz-api-1",
        "card_id": card.id,
        "merchant_name": "API MERCHANT",
        "mcc": "5812",
        "amount_cents": 5_000,
    }
    first = client.post("/authorizations", json=payload)
    second = client.post("/authorizations", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.get_json()["id"] == second.get_json()["id"]


def test_create_authorization_validation(app):
    response = app.test_client().post(
        "/authorizations", json={"idempotency_key": "authz-api-2"}
    )
    assert response.status_code == 400


def test_create_authorization_unknown_card(app):
    payload = {
        "idempotency_key": "authz-api-3",
        "card_id": 999_999,
        "merchant_name": "API MERCHANT",
        "mcc": "5812",
        "amount_cents": 5_000,
    }
    response = app.test_client().post("/authorizations", json=payload)
    assert response.status_code == 404
