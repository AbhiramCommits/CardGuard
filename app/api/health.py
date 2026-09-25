from flask import Blueprint, Response, current_app, jsonify
from sqlalchemy import text

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz() -> Response:
    return jsonify({"status": "ok"})


@bp.get("/readyz")
def readyz() -> Response | tuple[Response, int]:
    try:
        with current_app.extensions["engine"].connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return jsonify({"status": "unavailable"}), 503
    return jsonify({"status": "ready"})
