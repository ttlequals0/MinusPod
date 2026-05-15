"""Tag routes: /tags/* endpoints for the community-pattern tag vocabulary."""
import logging

from api import api, log_request, json_response
from utils.community_tags import vocabulary_payload

logger = logging.getLogger('podcast.api')


@api.route('/tags/vocabulary', methods=['GET'])
@log_request
def get_tag_vocabulary():
    """Return the canonical 49-tag vocabulary (cached) so the frontend can
    render the grouped tag picker without re-parsing the seed CSV.
    """
    return json_response(vocabulary_payload())
