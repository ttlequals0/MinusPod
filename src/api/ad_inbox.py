"""Ad Inbox routes — thin HTTP layer around ``ad_inbox_service``."""
import logging

from flask import request

from api import api, log_request, json_response, error_response, get_database
from ad_inbox_service import (
    enumerate_inbox_items,
    VALID_INBOX_STATUSES,
)

logger = logging.getLogger('podcast.api')


@api.route('/ad-inbox', methods=['GET'])
@log_request
def get_ad_inbox():
    """Return the Ad Inbox queue with status filter + pagination.

    Query params:
        status   pending|confirmed|rejected|adjusted|all  (default 'pending')
        limit    1-200                                    (default 50)
        offset   ≥0                                       (default 0)
    """
    db = get_database()

    status_filter = (request.args.get('status') or 'pending').lower()
    if status_filter not in VALID_INBOX_STATUSES:
        return error_response(
            f"status must be one of: {', '.join(sorted(VALID_INBOX_STATUSES))}",
            400)

    try:
        limit = int(request.args.get('limit', '50'))
    except ValueError:
        return error_response('limit must be an integer', 400)
    limit = max(1, min(limit, 200))

    try:
        offset = int(request.args.get('offset', '0'))
    except ValueError:
        return error_response('offset must be an integer', 400)
    offset = max(0, offset)

    counts = {'pending': 0, 'confirmed': 0, 'rejected': 0, 'adjusted': 0}
    matched: list[dict] = []
    for item in enumerate_inbox_items(db):
        counts[item['status']] = counts.get(item['status'], 0) + 1
        if status_filter == 'all' or item['status'] == status_filter:
            matched.append(item)

    total = len(matched)
    page = matched[offset:offset + limit]

    return json_response({
        'items': page,
        'total': total,
        'limit': limit,
        'offset': offset,
        'status': status_filter,
        'counts': counts,
    })
