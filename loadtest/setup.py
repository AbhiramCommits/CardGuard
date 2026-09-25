import json
import os
from pathlib import Path

from sqlalchemy import delete, select

from app.config import Config
from app.db import make_engine, make_session_factory
from app.models import Authorization, Card, LedgerEntry, LedgerPosting, SpendPolicy

RESULTS_DIR = Path(__file__).resolve().parent / "results"
CONFIG_PATH = RESULTS_DIR / "loadtest-config.json"

PROFILES = {
    1: {
        "name": "approve",
        "monthly_limit_cents": 10_000_000_000,
        "per_transaction_limit_cents": 100_000,
        "blocked_mccs": [],
        "velocity_max_auths": 1_000_000,
    },
    2: {
        "name": "decline",
        "monthly_limit_cents": 10_000_000_000,
        "per_transaction_limit_cents": 100_000,
        "blocked_mccs": ["5541"],
        "velocity_max_auths": 1_000_000,
    },
    3: {
        "name": "review",
        "monthly_limit_cents": 10_000,
        "per_transaction_limit_cents": 100_000,
        "blocked_mccs": [],
        "velocity_max_auths": 1_000_000,
    },
}


def main():
    engine = make_engine(Config.DATABASE_URL)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        tokens = {}
        for card_id, profile in PROFILES.items():
            card = session.get(Card, card_id)
            if card is None:
                raise SystemExit(f"card {card_id} not found; run `make seed` first")
            policy = session.scalar(
                select(SpendPolicy).where(SpendPolicy.employee_id == card.employee_id)
            )
            if policy is None:
                raise SystemExit(f"no spend policy for card {card_id}")
            policy.monthly_limit_cents = profile["monthly_limit_cents"]
            policy.per_transaction_limit_cents = profile["per_transaction_limit_cents"]
            policy.blocked_mccs = profile["blocked_mccs"]
            policy.velocity_max_auths = profile["velocity_max_auths"]
            policy.velocity_window_minutes = 60

            authorization_ids = select(Authorization.id).where(
                Authorization.card_id == card_id
            )
            session.execute(
                delete(LedgerEntry).where(
                    LedgerEntry.authorization_id.in_(authorization_ids)
                )
            )
            session.execute(
                delete(LedgerPosting).where(
                    LedgerPosting.authorization_id.in_(authorization_ids)
                )
            )
            session.execute(
                delete(Authorization).where(Authorization.card_id == card_id)
            )
            tokens[profile["name"]] = card.token
        session.commit()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cards = [
        {"name": name, "token": tokens[name]}
        for name in ("approve", "decline", "review")
    ]
    api_key = os.environ.get("CARDGUARD_API_KEYS", "dev-key").split(",")[0].strip()
    CONFIG_PATH.write_text(
        json.dumps({"cards": cards, "api_key": api_key}, indent=2) + "\n"
    )
    print(f"loadtest config written to {CONFIG_PATH}")


if __name__ == "__main__":
    main()
