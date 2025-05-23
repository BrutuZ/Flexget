from argparse import ArgumentParser, ArgumentTypeError
from functools import partial

from rich.highlighter import ReprHighlighter
from sqlalchemy.orm.exc import NoResultFound

from flexget import options
from flexget.event import event
from flexget.manager import Session
from flexget.terminal import TerminalTable, console, disable_colors, table_parser

from . import db


def attribute_type(attribute):
    if attribute.count('=') != 1:
        raise ArgumentTypeError(
            f'Received attribute in wrong format: {attribute}, '
            'should be in keyword format like `imdb_id=tt1234567`'
        )
    name, value = attribute.split('=', 2)
    return {name: value}


def do_cli(manager, options):
    """Handle entry-list subcommand."""
    if hasattr(options, 'table_type') and options.table_type == 'porcelain':
        disable_colors()

    action_map = {
        'all': pending_list_lists,
        'list': pending_list_list,
        'show': pending_list_show,
        'approve': partial(pending_list_approve, approve=True),
        'reject': partial(pending_list_approve, approve=False),
        'del': pending_list_del,
        'add': pending_list_add,
        'purge': pending_list_purge,
    }

    action_map[options.list_action](options)


def pending_list_lists(options):
    """Show all pending lists."""
    with Session() as session:
        lists = db.get_pending_lists(session=session)
        header = ['#', 'List Name']
        table = TerminalTable(*header, table_type=options.table_type)
        for entry_list in lists:
            table.add_row(str(entry_list.id), entry_list.name)
    console(table)


def pending_list_list(options):
    """List pending list entries."""
    highlighter = ReprHighlighter()
    with Session() as session:
        try:
            pending_list = db.get_list_by_exact_name(options.list_name, session=session)
        except NoResultFound:
            console(f'Could not find pending list with name `{options.list_name}`')
            return
        header = ['#', 'Title', '# of fields', 'Approved']
        table = TerminalTable(*header, table_type=options.table_type)
        for entry in db.get_entries_by_list_id(
            pending_list.id, order_by='added', descending=True, session=session
        ):
            approved = highlighter(str(entry.approved))
            table.add_row(str(entry.id), entry.title, str(len(entry.entry)), approved)
    console(table)


def pending_list_show(options):
    with Session() as session:
        try:
            pending_list = db.get_list_by_exact_name(options.list_name, session=session)
        except NoResultFound:
            console(f'Could not find pending list with name {options.list_name}')
            return

        try:
            entry = db.get_entry_by_id(pending_list.id, int(options.entry), session=session)
        except NoResultFound:
            console(
                f'Could not find matching pending entry with ID {int(options.entry)} in list `{options.list_name}`'
            )
            return
        except ValueError:
            entry = db.get_entry_by_title(pending_list.id, options.entry, session=session)
            if not entry:
                console(
                    f'Could not find matching pending entry with title `{options.entry}` in list `{options.list_name}`'
                )
                return
        header = ['Field name', 'Value']
        table = TerminalTable(*header, table_type=options.table_type)
        for k, v in sorted(entry.entry.items()):
            table.add_row(k, str(v))
    console(table)


def pending_list_add(options):
    with Session() as session:
        try:
            pending_list = db.get_list_by_exact_name(options.list_name, session=session)
        except NoResultFound:
            console(f'Could not find a pending list with name `{options.list_name}`, creating')
            pending_list = db.PendingListList(name=options.list_name)
            session.add(pending_list)
        session.merge(pending_list)
        session.commit()
        title = options.entry_title
        entry = {'title': options.entry_title, 'url': options.url}
        db_entry = db.get_entry_by_title(list_id=pending_list.id, title=title, session=session)
        if db_entry:
            console(
                f'Entry with the title `{title}` already exist with list `{pending_list.name}`. Will replace identifiers if given'
            )
            operation = 'updated'
        else:
            console(f'Adding entry with title `{title}` to list `{pending_list.name}`')
            db_entry = db.PendingListEntry(entry=entry, pending_list_id=pending_list.id)
            if options.approved:
                console('marking entry as approved')
                db_entry.approved = True
            session.add(db_entry)
            operation = 'added'
        if options.attributes:
            console(f'Adding attributes to entry `{title}`')
            for identifier in options.attributes:
                entry.update(identifier)
            db_entry.entry = entry
        console(f'Successfully {operation} entry `{title}` to pending list `{pending_list.name}` ')


def pending_list_approve(options, approve=None):
    with Session() as session:
        try:
            entry_list = db.get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console(f'Could not find pending list with name `{options.list_name}`')
            return
        try:
            db_entry = db.get_entry_by_id(entry_list.id, int(options.entry), session=session)
        except NoResultFound:
            console(
                f'Could not find matching entry with ID {int(options.entry)} in list `{options.list_name}`'
            )
            return
        except ValueError:
            db_entry = db.get_entry_by_title(entry_list.id, options.entry, session=session)
            if not db_entry:
                console(
                    f'Could not find matching entry with title `{options.entry}` in list `{options.list_name}`'
                )
                return
        approve_text = 'approved' if approve else 'rejected'
        if (db_entry.approved is True and approve is True) or (
            db_entry.approved is False and approve is False
        ):
            console(f'entry {db_entry.title} is already {approve_text}')
            return
        db_entry.approved = approve
        console(f'Successfully marked pending entry {db_entry.title} as {approve_text}')


def pending_list_del(options):
    with Session() as session:
        try:
            entry_list = db.get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console(f'Could not find pending list with name `{options.list_name}`')
            return
        try:
            db_entry = db.get_entry_by_id(entry_list.id, int(options.entry), session=session)
        except NoResultFound:
            console(
                f'Could not find matching entry with ID {int(options.entry)} in list `{options.list_name}`'
            )
            return
        except ValueError:
            db_entry = db.get_entry_by_title(entry_list.id, options.entry, session=session)
            if not db_entry:
                console(
                    f'Could not find matching entry with title `{options.entry}` in list `{options.list_name}`'
                )
                return
        console(f'Removing entry `{db_entry.title}` from list {options.list_name}')
        session.delete(db_entry)


def pending_list_purge(options):
    with Session() as session:
        try:
            entry_list = db.get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console(f'Could not find entry list with name `{options.list_name}`')
            return
        console(f'Deleting list {options.list_name}')
        session.delete(entry_list)


@event('options.register')
def register_parser_arguments():
    # Common option to be used in multiple subparsers
    entry_parser = ArgumentParser(add_help=False)
    entry_parser.add_argument('entry_title', help='Title of the entry')
    entry_parser.add_argument('url', help='URL of the entry')

    global_entry_parser = ArgumentParser(add_help=False)
    global_entry_parser.add_argument('entry', help='Can be entry title or ID')

    attributes_parser = ArgumentParser(add_help=False)
    attributes_parser.add_argument(
        '--attributes',
        metavar='<attributes>',
        nargs='+',
        type=attribute_type,
        help='Can be a string or a list of string with the format imdb_id=XXX, tmdb_id=XXX, etc',
    )
    list_name_parser = ArgumentParser(add_help=False)
    list_name_parser.add_argument(
        'list_name', nargs='?', default='pending', help='Name of pending list to operate on'
    )
    # Register subcommand
    parser = options.register_command('pending-list', do_cli, help='View and manage pending lists')
    # Set up our subparsers
    subparsers = parser.add_subparsers(title='actions', metavar='<action>', dest='list_action')
    subparsers.add_parser('all', help='Shows all existing pending lists', parents=[table_parser])
    subparsers.add_parser(
        'list',
        parents=[list_name_parser, table_parser],
        help='List pending entries from a pending list',
    )
    subparsers.add_parser(
        'show',
        parents=[list_name_parser, global_entry_parser, table_parser],
        help='Show entry fields.',
    )
    add = subparsers.add_parser(
        'add',
        parents=[list_name_parser, entry_parser, attributes_parser],
        help='Add a entry to a pending list',
    )
    add.add_argument('--approved', action='store_true', help='Add an entry as approved')
    subparsers.add_parser(
        'approve',
        parents=[list_name_parser, global_entry_parser],
        help='Mark a pending entry as approved',
    )
    subparsers.add_parser(
        'reject',
        parents=[list_name_parser, global_entry_parser],
        help='Mark a pending entry as rejected',
    )
    subparsers.add_parser(
        'del',
        parents=[list_name_parser, global_entry_parser],
        help='Remove an entry from a pending list using its title or ID',
    )
    subparsers.add_parser(
        'purge',
        parents=[list_name_parser],
        help='Removes an entire pending list with all of its entries. Use this with caution',
    )
