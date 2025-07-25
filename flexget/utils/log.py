"""Logging utilities."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import Column, DateTime, Index, Integer, String

from flexget import db_schema
from flexget.event import event
from flexget.utils.database import with_session
from flexget.utils.sqlalchemy_utils import table_schema

logger = logger.bind(name='util.log')

if TYPE_CHECKING:
    import loguru
    from sqlalchemy.orm import Session

    Base = object
else:
    Base = db_schema.versioned_base('log_once', 0)


@db_schema.upgrade('log_once')
def upgrade(ver: int | None, session: Session):
    if ver is None:
        logger.info('Adding index to md5sum column of log_once table.')
        table = table_schema('log_once', session)
        Index('log_once_md5sum', table.c.md5sum, unique=True).create(bind=session.bind)
        ver = 0
    return ver


class LogMessage(Base):
    """Declarative."""

    __tablename__ = 'log_once'

    id = Column(Integer, primary_key=True)
    md5sum = Column(String, unique=True)
    added = Column(DateTime, default=datetime.now())

    def __init__(self, md5sum: str) -> None:
        self.md5sum = md5sum

    def __repr__(self) -> str:
        return f"<LogMessage('{self.md5sum}')>"


@event('manager.db_cleanup')
def purge(manager, session: Session) -> None:
    """Purge old messages from database."""
    old = datetime.now() - timedelta(days=365)

    result = session.query(LogMessage).filter(LogMessage.added < old).delete()
    if result:
        logger.verbose('Purged {} entries from log_once table.', result)


@with_session
def log_once(
    message: str,
    logger: loguru.Logger | None = None,
    once_level: str = 'INFO',
    suppressed_level: str = 'VERBOSE',
    session: Session = None,
) -> bool | None:
    """Log message only once using given logger`.

    Return False if suppressed logging.
    When suppressed, `suppressed_level` level is still logged.
    """
    if logger is None:
        logger = globals()['logger'].bind(name='log_once')
    # If there is no active manager, don't access the db
    from flexget.manager import manager

    if not manager:
        logger.warning('DB not initialized. log_once will not work properly.')
        logger.log(once_level, message)
        return None

    digest = hashlib.md5()
    digest.update(message.encode('latin1', 'replace'))
    md5sum = digest.hexdigest()

    # abort if this has already been logged
    if session.query(LogMessage).filter_by(md5sum=md5sum).first():
        logger.log(suppressed_level, message)
        return False

    row = LogMessage(md5sum)
    session.add(row)
    session.flush()

    logger.log(once_level, message)
    return True
