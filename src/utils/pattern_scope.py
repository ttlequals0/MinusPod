"""Scope and community-tag gate shared by the text and audio matchers."""
from utils.community_tags import UNIVERSAL_TAG


def pattern_passes_scope(scope, pattern_network_id, pattern_podcast_id, source, sponsor_tags, *,
                         podcast_id, network_id, podcast_tag_set):
    """True when a pattern applies to this podcast; community patterns also need tag eligibility."""
    if scope == 'network':
        if not (network_id and pattern_network_id == network_id):
            return False
    elif scope == 'podcast':
        if not (podcast_id and pattern_podcast_id == podcast_id):
            return False
    elif scope != 'global':
        return False
    if source != 'community':
        return True
    return (UNIVERSAL_TAG in sponsor_tags or not sponsor_tags or not podcast_tag_set
            or bool(sponsor_tags & podcast_tag_set))
