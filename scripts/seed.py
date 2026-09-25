import random

from sqlalchemy import func, select

from app.config import Config
from app.db import make_engine, make_session_factory
from app.models import Account, AccountType, Card, Company, Employee, SpendPolicy

COMPANIES = ["Nimbus Systems", "Quarry Logistics", "Vertex Labs"]
EMPLOYEES_PER_COMPANY = [9, 8, 8]
TOTAL_EMPLOYEES = 25
MCC_POOL = [
    "5411", "5812", "5814", "5541", "4511", "4111",
    "3504", "4411", "4722", "7832", "5462", "4121",
]


def seed(session):
    existing = session.scalar(select(func.count()).select_from(Company))
    if existing:
        print(f"database already has {existing} companies, skipping seed")
        return

    rng = random.Random(42)
    companies = [Company(name=name) for name in COMPANIES]
    session.add_all(companies)
    session.flush()

    for company in companies:
        for account_type in AccountType:
            session.add(Account(company_id=company.id, account_type=account_type))

    session.flush()
    counter = 0
    for company, headcount in zip(companies, EMPLOYEES_PER_COMPANY):
        slug = company.name.lower().replace(" ", "")
        for _ in range(headcount):
            counter += 1
            employee = Employee(
                company_id=company.id,
                name=f"Employee {counter:02d}",
                email=f"employee{counter:02d}@{slug}.example.com",
            )
            session.add(employee)
            session.flush()
            session.add(
                Card(employee_id=employee.id, last_four=f"{counter:04d}", token=f"card_tok_{counter:04d}")
            )
            session.add(
                SpendPolicy(
                    employee_id=employee.id,
                    monthly_limit_cents=500_000 + rng.randrange(0, 500_000, 10_000),
                    per_transaction_limit_cents=100_000 + rng.randrange(0, 400_000, 10_000),
                    blocked_mccs=rng.sample(MCC_POOL, k=3),
                    velocity_max_auths=rng.choice([5, 10, 15, 20]),
                    velocity_window_minutes=rng.choice([5, 10, 15, 30, 60]),
                )
            )
    session.commit()
    print(
        f"seeded {len(companies)} companies, {counter} employees, "
        f"{counter} cards, {counter} spend policies"
    )
    assert counter == TOTAL_EMPLOYEES


def main():
    engine = make_engine(Config.DATABASE_URL)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        seed(session)
    engine.dispose()


if __name__ == "__main__":
    main()
