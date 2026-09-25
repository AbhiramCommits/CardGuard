from app.api.health import bp as health_bp
from app.api.v1.accounts import bp as accounts_v1_bp
from app.api.v1.authorizations import bp as authorizations_v1_bp


def register_blueprints(app):
    app.register_blueprint(health_bp)
    app.register_blueprint(authorizations_v1_bp, url_prefix="/v1/authorizations")
    app.register_blueprint(accounts_v1_bp, url_prefix="/v1/accounts")
