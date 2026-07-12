"""Cross-episode ad detection review: /detections endpoint."""
import logging

from flask import request

from api import (
    api, log_request, json_response, error_response,
    get_database,
)
from detection_review import (
    filter_detections, flatten_detections, paginate, sort_detections,
)

logger = logging.getLogger('podcast.api')

VALID_STATUS = {'needs_review', 'pending', 'rejected', 'accepted', 'all'}
VALID_SORT = {'date', 'confidence', 'podcast'}
VALID_ORDER = {'asc', 'desc'}


@api.route('/detections', methods=['GET'])
@log_request
def list_detections():
    """List ad detections across all feeds with filter/sort/pagination."""
    db = get_database()

    limit = min(max(1, request.args.get('limit', 20, type=int)), 100)
    page = max(1, request.args.get('page', 1, type=int))
    status = request.args.get('status', 'needs_review')
    if status not in VALID_STATUS:
        return error_response(f"Invalid status '{status}'", 400)
    feed = request.args.get('feed') or None
    q = request.args.get('q') or None
    sort = request.args.get('sort', 'date')
    if sort not in VALID_SORT:
        return error_response(f"Invalid sort '{sort}'", 400)
    order = request.args.get('order', 'desc')
    if order not in VALID_ORDER:
        return error_response(f"Invalid order '{order}'", 400)

    rows = db.get_detection_rows()
    corrections = db.get_review_corrections()
    items = flatten_detections(rows, corrections)
    items = filter_detections(items, status=status, feed=feed, q=q)
    items = sort_detections(items, sort=sort, order=order)
    page_items, total, total_pages, page = paginate(items, page, limit)

    return json_response({
        'detections': page_items,
        'total': total,
        'page': page,
        'totalPages': total_pages,
        'limit': limit,
    })
