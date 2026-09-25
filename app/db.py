from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool


def make_engine(url, echo=False, pool_size=10, max_overflow=20):
    return create_engine(
        url,
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=echo,
    )


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
