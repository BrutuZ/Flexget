"""Miscellaneous SQLAlchemy helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import ColumnDefault, Index, Sequence, text
from sqlalchemy.exc import NoSuchTableError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.schema import MetaData, Table
from sqlalchemy.types import TypeEngine

if TYPE_CHECKING:
    from typing_extensions import Self

logger = logger.bind(name='sql_utils')


def table_exists(name: str, session: Session) -> bool:
    """Use SQLAlchemy reflect to check table existences.

    :param string name: Table name to check
    :param Session session: Session to use
    :return: True if table exists, False otherwise
    :rtype: bool
    """
    try:
        table_schema(name, session)
    except NoSuchTableError:
        return False
    return True


def index_exists(table_name: str, index_name: str, session: Session) -> bool:
    """Use SQLAlchemy reflect to check index existences.

    :param string table_name: Table name to check
    :param string index_name: Index name to check
    :param Session session: Session to use
    :return: True if table exists, False otherwise
    :rtype: bool
    """
    try:
        return bool(table_index(table_name, index_name, session))
    except NoSuchTableError:
        return False


def table_schema(name: str, session: Session) -> Table:
    """Return Table schema using SQLAlchemy reflect as it currently exists in the db."""
    return Table(name, MetaData(), autoload_with=session.bind)


def table_columns(table: str | Table, session: Session) -> list[str]:
    """Return list of column names in the table or empty list.

    :param string table: Name of table or table schema
    :param Session session: SQLAlchemy Session
    """
    if isinstance(table, str):
        table = table_schema(table, session)
    return [column.name for column in table.columns]


def table_index(table_name: str, index_name: str, session: Session) -> Index:
    """Find an index by table name and index name.

    :param string table_name: Name of table
    :param string index_name: Name of the index
    :param Session session: SQLAlchemy Session
    :returns: The requested index
    """
    table = table_schema(table_name, session)
    return get_index_by_name(table, index_name)


def drop_index(table_name: str, index_name: str, session: Session) -> None:
    """Drop an index by table name and index name.

    :param string table_name: Name of table
    :param string index_name: Name of the index
    :param Session session: SQLAlchemy Session
    """
    index = table_index(table_name, index_name, session)
    index.drop(bind=session.bind)


def table_add_column(
    table: Table | str,
    name: str,
    col_type: TypeEngine | type,
    session: Session,
    default: Any = None,
) -> None:
    """Add a column to a table.

    .. warning:: Uses raw statements, probably needs to be changed in
                 order to work on other databases besides SQLite

    :param string table: Table to add column to (can be name or schema)
    :param string name: Name of new column to add
    :param col_type: The sqlalchemy column type to add
    :param Session session: SQLAlchemy Session to do the alteration
    :param default: Default value for the created column (optional)
    """
    if isinstance(table, str):
        table = table_schema(table, session)
    if name in table_columns(table, session):
        # If the column already exists, we don't have to do anything.
        return
    # Add the column to the table
    if not isinstance(col_type, TypeEngine):
        # If we got a type class instead of an instance of one, instantiate it
        col_type = col_type()
    type_string = session.bind.engine.dialect.type_compiler.process(col_type)
    statement = f'ALTER TABLE {table.name} ADD {name} {type_string}'
    session.execute(text(statement))
    session.commit()
    # Update the table with the default value if given
    if default is not None:
        # Get the new schema with added column
        table = table_schema(table.name, session)
        if not isinstance(default, (ColumnDefault, Sequence)):
            default = ColumnDefault(default)
        default._set_parent(getattr(table.c, name))
        statement = table.update().values({name: default.execute(bind=session.bind)})
        session.execute(statement)
        session.commit()


def drop_tables(names: list[str], session: Session) -> None:
    """Take a list of table names and drops them from the database if they exist."""
    metadata = MetaData()
    metadata.reflect(bind=session.bind)
    for table in metadata.sorted_tables:
        if table.name in names:
            table.drop()


def get_index_by_name(table: Table, name: str) -> Index | None:
    """Find declaratively defined index from table by name.

    :param table: Table object
    :param string name: Name of the index to get
    :return: Index object
    """
    for index in table.indexes:
        if index.name == name:
            return index
    return None


def create_index(table_name: str, session: Session, *column_names: str) -> None:
    """Create an index on specified `columns` in `table_name`.

    :param table_name: Name of table to create the index on.
    :param session: Session object which should be used
    :param column_names: The names of the columns that should belong to this index.
    """
    index_name = '_'.join(['ix', table_name, *list(column_names)])
    table = table_schema(table_name, session)
    columns = [getattr(table.c, column) for column in column_names]
    try:
        Index(index_name, *columns).create(bind=session.bind)
    except OperationalError:
        logger.opt(exception=True).debug('Error creating index.')


class ContextSession(Session):
    """:class:`sqlalchemy.orm.Session` which automatically commits when used as context manager without errors."""

    # TODO: This auto-committing might be a bad idea and need to be removed
    # might be hard to figure out where exactly code needs to be updated to compensate though.

    def __enter__(self) -> Self:
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            super().__exit__(exc_type, exc_val, exc_tb)
