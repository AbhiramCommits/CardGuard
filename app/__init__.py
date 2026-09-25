from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def create_app(config_object: str | type[object] | None = None) -> Flask:
    from flask import Flask

    from app.db import make_engine, make_session_factory
    from app.observability import install_json_logging

    app = Flask(__name__)
    if config_object is None:
        config_object = os.environ.get("APP_CONFIG", "app.config.Config")
    app.config.from_object(config_object)

    install_json_logging()

    engine = make_engine(app.config["DATABASE_URL"])
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = make_session_factory(engine)

    from app.risk.model import risk_model

    risk_model.load(app.config["MODEL_PATH"])

    from app.api import register_blueprints

    register_blueprints(app)

    return app
