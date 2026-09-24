from app.api.authorizations import bp as authorizations_bp
from app.api.health import bp as health_bp


def register_blueprints(app):
    app.register_blueprint(health_bp)
    app.register_blueprint(authorizations_bp, url_prefix="/authorizations")
