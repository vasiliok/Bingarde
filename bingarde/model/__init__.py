"""SQLAlchemy setup shared by the whole app.

TurboGears calls ``init_model(engine)`` once at startup. The transaction
manager (``tm.enabled``) commits ``DBSession`` at the end of each request, or
rolls it back when the request raises.
"""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker
from zope.sqlalchemy import register

maker = sessionmaker(autoflush=True)
DBSession = scoped_session(maker)
register(DBSession)

# Stable constraint names, so Alembic migrations stay readable and diffable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Range of the integer columns (ids, build numbers). Larger values from a
# request must not reach the database, which would fail with an error.
INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1


def db_int(value):
    """``value`` as an int in the integer column range, or None."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if INT_MIN <= number <= INT_MAX else None


def get_row(cls, ident):
    """The ``cls`` row whose id is ``ident`` (a request value of any kind), or None."""
    pk = db_int(ident)
    return DBSession.get(cls, pk) if pk is not None else None


def init_model(engine):
    """Bind the session to the engine. The schema itself comes from Alembic."""
    DBSession.configure(bind=engine)


# Import the mapped classes so ``Base.metadata`` knows every table.
from bingarde.model.auth import ApiToken, User  # noqa: E402,F401
from bingarde.model.build import FilterPreset, Build, BuildFile  # noqa: E402,F401
