from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def create_app(config_object: str | type[object] | None = None) -> Flask:
    from flask import Flask

    from app.db import make_engine, make_session_factory

    app = Flask(__name__)
    if config_object is None:
        config_object = os.environ.get("APP_CONFIG", "app.config.Config")
    app.config.from_object(config_object)

    _configure_logging()

    engine = make_engine(app.config["DATABASE_URL"])
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = make_session_factory(engine)

    from app.risk.model import risk_model

    risk_model.load(app.config["MODEL_PATH"])

    from app.api import register_blueprints

    register_blueprints(app)

    return app


def _configure_logging() -> None:
    logger = logging.getLogger("cardguard")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
