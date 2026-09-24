from flask import Blueprint, jsonify

bp = Blueprint("health", __name__)


@bp.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})
