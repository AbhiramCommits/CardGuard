from flask import Blueprint, Response, current_app, jsonify

from app.ledger import account_balance
from app.models import Account

bp = Blueprint("accounts_v1", __name__)


@bp.get("/<int:account_id>/balance")
def get_balance(account_id: int) -> Response | tuple[Response, int]:
    session_factory = current_app.extensions["session_factory"]
    with session_factory() as session:
        account = session.get(Account, account_id)
        if account is None:
            return jsonify({"error": "account not found"}), 404
        return jsonify(
            {
                "account_id": account.id,
                "account_type": account.account_type.value,
                "balance_cents": account_balance(session, account.id),
            }
        )
