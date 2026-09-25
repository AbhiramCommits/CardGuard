from flask import Blueprint, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

bp = Blueprint("metrics", __name__)


@bp.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
