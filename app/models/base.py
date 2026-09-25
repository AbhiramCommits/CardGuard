import enum

from sqlalchemy.orm import DeclarativeBase


def enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class Base(DeclarativeBase):
    pass
