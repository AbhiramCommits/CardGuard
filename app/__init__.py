import os

from flask import Flask

from app.db import make_engine, make_session_factory


def create_app(config_object=None):
    app = Flask(__name__)
    if config_object is None:
        config_object = os.environ.get("APP_CONFIG", "app.config.Config")
    app.config.from_object(config_object)

    engine = make_engine(app.config["DATABASE_URL"])
    app.extensions["engine"] = engine
    app.extensions["session_factory"] = make_session_factory(engine)

    from app.api import register_blueprints

    register_blueprints(app)

    return app
