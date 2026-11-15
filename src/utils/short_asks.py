"""Short host asks and sponsor thanks (snip_short_asks): flag, floor and sentence-edge helpers."""
import logging

from utils.constants import LEARNING_SHOW_PROMO_FLOORS, show_promo_floor_problem

logger = logging.getLogger(__name__)

SHORT_ASK_CATEGORIES = ('interaction', 'self_promo', 'sponsor')
SHORT_ASK_MIN_S = 2.0
SHORT_ASK_MAX_S = 40.0
SHORT_ASK_EDGE_CAP_S = 1.5
SHORT_ASK_TIGHT_BELOW_S = 3.0
SHORT_ASK_TIGHT_EDGE_CAP_S = 0.5
# Learn floors (seconds, characters) of a short-ask show pattern; both categories sit at or above SHORT_ASK_MIN_S.
SHORT_ASK_LEARN_FLOORS = {'interaction': (4, 30), 'self_promo': (4, 30)}
# Least fuzzy score at which a short-ask show pattern matches a transcript.
SHORT_ASK_PATTERN_MIN_SCORE = 90.0
_EPS = 0.05
_SENTENCE_END = ('.', '?', '!')
_CLOSERS = '"\')]}”’'


def _origin(ad):
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
    origin = _origin(ad)
    if origin is None:
        return False
    start, end = ad.get('start'), ad.get('end')
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    cap = SHORT_ASK_EDGE_CAP_S + _EPS
    return (SHORT_ASK_MIN_S - _EPS <= end - start <= SHORT_ASK_MAX_S + _EPS
            and abs(start - origin[0]) <= cap and abs(end - origin[1]) <= cap)


def floor_for(ad, default):
    """Duration floor for this marker: the short-ask floor when flagged, else the caller's default."""
    return min(default, SHORT_ASK_MIN_S) if is_short_ask(ad) else default


def _ends_sentence(text):
    return (text or '').strip().rstrip(_CLOSERS).endswith(_SENTENCE_END)


def _edges(segments):
    """(starts, ends) of sentences over the shared transcript lines."""
    from ad_detector.boundaries import timed_line_segments  # lazy: ad_detector imports this module
    lines = sorted((s for s in timed_line_segments(segments or []) if 'start' in s and 'end' in s),
                   key=lambda s: s['start'])
    starts, ends = [], []
    for i, line in enumerate(lines):
        if i == 0 or _ends_sentence(lines[i - 1].get('text')):
            starts.append(float(line['start']))
        if _ends_sentence(line.get('text')) or i == len(lines) - 1:
            ends.append(float(line['end']))
    return starts, ends


def _snap(candidates, origin, current, sign):
    """Nearest candidate within the cap of the LLM edge; an edge a reviewer trimmed inward never snaps back out."""
    floor = current * sign - _EPS if current * sign > origin * sign + _EPS else float('-inf')
    near = [c for c in candidates
            if abs(c - origin) <= SHORT_ASK_EDGE_CAP_S + _EPS and c * sign >= floor]
    return min(near, key=lambda c: (abs(c - current), abs(c - origin))) if near else None


def clamp_short_ask(ad, segments):
    """Snap a flagged marker to sentence edges within the cap of its LLM edges, else drop the flag."""
    if ad.get('short_ask') is not True:
        return ad
    origin = _origin(ad)
    if origin is None or ad.get('category') not in SHORT_ASK_CATEGORIES:
        return _unflag(ad)
    starts, ends = _edges(segments)
    start = _snap(starts, origin[0], ad['start'], 1)
    end = _snap(ends, origin[1], ad['end'], -1)
    if (start is None or end is None
            or not SHORT_ASK_MIN_S - _EPS <= end - start <= SHORT_ASK_MAX_S + _EPS):
        return _unflag(ad)
    if end - start < SHORT_ASK_TIGHT_BELOW_S and not _edges_tight(origin, start, end):
        return _unflag(ad)
    ad['start'], ad['end'] = start, end
    return ad


def _edges_tight(origin, start, end):
    """Spans under 3 s keep the flag only when both edges sit on a sentence edge within 0.5 s of the LLM's."""
    cap = SHORT_ASK_TIGHT_EDGE_CAP_S + _EPS
    return abs(start - origin[0]) <= cap and abs(end - origin[1]) <= cap


def _unflag(ad):
    ad.pop('short_ask', None)
    ad.pop('short_ask_origin', None)
    return ad


def feed_snips_short_asks(db, slug):
    """Whether the feed resolves snip_short_asks on; off when it cannot be read."""
    try:
        return bool(slug) and db.resolve_feed_bool(slug, 'snip_short_asks', 'snip_short_asks') is True
    except Exception as e:
        logger.warning(f"[{slug}] snip_short_asks could not be resolved, treating as off: {e}")
        return False


def learn_floor_problem(category, duration, chars, max_duration):
    """Why a span cannot become a short-ask show pattern, or None; the configured minimum does not apply."""
    floor_s, floor_chars = SHORT_ASK_LEARN_FLOORS[category]
    if duration < floor_s:
        return f"duration {duration:.0f}s is below the {floor_s}s short-ask floor"
    if duration > max_duration:
        return f"duration {duration:.0f}s exceeds the {max_duration}s maximum"
    if chars < floor_chars:
        return f"text length {chars} is below the {floor_chars} character short-ask floor"
    return None


def show_floor_problem(db, podcast_id, category, duration, chars, min_duration, max_duration):
    """show_promo_floor_problem, with the short-ask learn floors in place of its table while the feed is on."""
    if category in SHORT_ASK_LEARN_FLOORS and feed_snips_short_asks(db, podcast_id):
        return learn_floor_problem(category, duration, chars, max_duration)
    return show_promo_floor_problem(category, duration, chars, min_duration, max_duration)


def is_short_show_template(category, template):
    """A show template under its category's PR floor in characters is a short-ask pattern by construction."""
    if category not in SHORT_ASK_LEARN_FLOORS:
        return False
    return len(template or '') < LEARNING_SHOW_PROMO_FLOORS[category][1]
