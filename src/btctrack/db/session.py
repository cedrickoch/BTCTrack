from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from btctrack.config import get_settings
from btctrack.db.models import Base


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(settings.db_url, echo=False, future=True)
    Base.metadata.create_all(engine)
    return engine


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """For tests / DB-path changes."""
    get_engine.cache_clear()
    _session_factory.cache_clear()
