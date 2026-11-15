"""Short host asks and sponsor thanks (snip_short_asks): flag, floor and sentence-edge helpers."""

SHORT_ASK_CATEGORIES = ('interaction', 'self_promo', 'sponsor')
SHORT_ASK_MIN_S = 2.0
SHORT_ASK_MAX_S = 40.0
SHORT_ASK_EDGE_CAP_S = 1.5
SHORT_ASK_TIGHT_BELOW_S = 3.0
SHORT_ASK_TIGHT_EDGE_CAP_S = 0.5
EPS = 0.05


def origin_of(ad):
    origin = ad.get('short_ask_origin')
    if (isinstance(origin, (list, tuple)) and len(origin) == 2
            and all(isinstance(v, (int, float)) for v in origin)):
        return float(origin[0]), float(origin[1])
    return None


def stamp_short_ask(ad, enabled):
    """Detector hook: keep the LLM's short_ask flag only when the feed is on, the category and length fit."""
    if not ad.get('short_ask'):
        ad.pop('short_ask', None)
        return ad
    duration = ad['end'] - ad['start']
    if (enabled and ad.get('category') in SHORT_ASK_CATEGORIES
            and SHORT_ASK_MIN_S <= duration <= SHORT_ASK_MAX_S):
        ad['short_ask'] = True
        ad['short_ask_origin'] = [ad['start'], ad['end']]
    else:
        ad.pop('short_ask', None)
        ad.pop('short_ask_origin', None)
    return ad


def is_short_ask(ad):
    """Whether this marker is a flagged short ask still within its length and edge-move caps."""
    if ad.get('short_ask') is not True or ad.get('category') not in SHORT_ASK_CATEGORIES:
        return False
    origin = origin_of(ad)
    if origin is None:
        return False
    start, end = ad.get('start'), ad.get('end')
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    cap = SHORT_ASK_EDGE_CAP_S + EPS
    return (SHORT_ASK_MIN_S - EPS <= end - start <= SHORT_ASK_MAX_S + EPS
            and abs(start - origin[0]) <= cap and abs(end - origin[1]) <= cap)


def floor_for(ad, default):
    """Duration floor for this marker: the short-ask floor when flagged, else the caller's default."""
    return min(default, SHORT_ASK_MIN_S) if is_short_ask(ad) else default


def unflag(ad):
    ad.pop('short_ask', None)
    ad.pop('short_ask_origin', None)
    return ad
