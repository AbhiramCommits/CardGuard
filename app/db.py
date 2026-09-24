from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(url, echo=False):
    return create_engine(url, pool_pre_ping=True, echo=echo)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
