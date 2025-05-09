from math import ceil

from flask import jsonify, request
from loguru import logger
from sqlalchemy.orm.exc import NoResultFound

from flexget.api import APIResource, api
from flexget.api.app import (
    NotFoundError,
    base_message_schema,
    etag,
    pagination_headers,
    success_response,
)

from . import db

logger = logger.bind(name='failed_api')

retry_failed_api = api.namespace('failed', description='View and manage failed entries')


class ObjectsContainer:
    retry_failed_entry_object = {
        'type': 'object',
        'properties': {
            'id': {'type': 'integer'},
            'title': {'type': 'string'},
            'url': {'type': 'string'},
            'added_at': {'type': 'string', 'format': 'date-time'},
            'reason': {'type': 'string'},
            'count': {'type': 'integer'},
            'retry_time': {'type': ['string', 'null'], 'format': 'date-time'},
        },
        'required': ['id', 'title', 'url', 'added_at', 'reason', 'count', 'retry_time'],
        'additionalProperties': False,
    }
    retry_entries_list_object = {'type': 'array', 'items': retry_failed_entry_object}


retry_failed_entry_schema = api.schema_model(
    'retry_failed_entry_schema', ObjectsContainer.retry_failed_entry_object
)
retry_entries_list_schema = api.schema_model(
    'retry_entries_list_schema', ObjectsContainer.retry_entries_list_object
)

sort_choices = ('failure_time', 'id', 'title', 'url', 'reason', 'count', 'retry_time')
failed_parser = api.pagination_parser(sort_choices=sort_choices)


@retry_failed_api.route('/')
class RetryFailed(APIResource):
    @etag
    @api.response(NotFoundError)
    @api.response(200, model=retry_entries_list_schema)
    @api.doc(expect=[failed_parser])
    def get(self, session=None):
        """List all failed entries."""
        args = failed_parser.parse_args()

        # Pagination and sorting params
        page = args['page']
        per_page = args['per_page']
        sort_by = args['sort_by']
        sort_order = args['order']

        if sort_by == 'failure_time':
            sort_by = 'tof'

        # Handle max size limit
        per_page = min(per_page, 100)

        descending = sort_order == 'desc'

        # Handle max size limit
        per_page = min(per_page, 100)

        start = per_page * (page - 1)
        stop = start + per_page

        kwargs = {
            'start': start,
            'stop': stop,
            'descending': descending,
            'sort_by': sort_by,
            'session': session,
        }

        total_items = db.get_failures(session, count=True)

        if not total_items:
            return jsonify([])

        failed_entries = [failed.to_dict() for failed in db.get_failures(**kwargs)]

        total_pages = ceil(total_items / float(per_page))

        if page > total_pages:
            raise NotFoundError(f'page {page} does not exist')

        # Actual results in page
        actual_size = min(per_page, len(failed_entries))

        # Get pagination headers
        pagination = pagination_headers(total_pages, total_items, actual_size, request)

        # Created response
        rsp = jsonify(failed_entries)

        # Add link header to response
        rsp.headers.extend(pagination)

        return rsp

    @api.response(200, 'successfully deleted failed entry', model=base_message_schema)
    def delete(self, session=None):
        """Clear all failed entries."""
        logger.debug('deleting all failed entries')
        deleted = session.query(db.FailedEntry).delete()
        return success_response(f'successfully deleted {deleted} failed entries')


@retry_failed_api.route('/<int:failed_entry_id>/')
@api.response(NotFoundError)
class RetryFailedID(APIResource):
    @etag
    @api.doc(params={'failed_entry_id': 'ID of the failed entry'})
    @api.response(200, model=retry_failed_entry_schema)
    def get(self, failed_entry_id, session=None):
        """Get failed entry by ID."""
        try:
            failed_entry = (
                session.query(db.FailedEntry).filter(db.FailedEntry.id == failed_entry_id).one()
            )
        except NoResultFound:
            raise NotFoundError(f'could not find entry with ID {failed_entry_id}')
        return jsonify(failed_entry.to_dict())

    @api.response(200, 'successfully delete failed entry', model=base_message_schema)
    def delete(self, failed_entry_id, session=None):
        """Delete failed entry by ID."""
        try:
            failed_entry = (
                session.query(db.FailedEntry).filter(db.FailedEntry.id == failed_entry_id).one()
            )
        except NoResultFound:
            raise NotFoundError(f'could not find entry with ID {failed_entry_id}')
        logger.debug('deleting failed entry: "{}"', failed_entry.title)
        session.delete(failed_entry)
        return success_response(f'successfully delete failed entry {failed_entry_id}')
