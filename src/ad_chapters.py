"""Ad chapters: segments left in the served audio published as their own
Podcasting 2.0 chapters so a chapter-aware player can skip them."""
from dataclasses import dataclass

from config import (
    AD_CHAPTER_KINDS, AD_CHAPTER_SNAP_SECONDS, CHAPTERS_MODE_OFF,
    DEFAULT_AD_CHAPTER_CATEGORIES, is_pending_review,
    normalize_segment_category, resolve_ad_chapters_enabled,
    resolve_chapters_mode,
)
from database.settings import registry_default
from utils.time import adjust_timestamp


@dataclass(frozen=True)
class AdChapterConfig:
    enabled: bool
    categories: dict
    include_held: bool
    title_format: str
    held_title_format: str
    resume_title: str
    min_confidence: float

    @classmethod
    def disabled(cls) -> 'AdChapterConfig':
        return cls(enabled=False, categories=dict(DEFAULT_AD_CHAPTER_CATEGORIES),
                   include_held=False,
                   title_format=registry_default('ad_chapter_title_format'),
                   held_title_format=registry_default('ad_chapter_held_title_format'),
                   resume_title=registry_default('ad_chapter_resume_title'),
                   min_confidence=float(registry_default('ad_chapter_min_confidence')))


def _setting(db, key) -> str:
    value = db.get_setting(key)
    return value if value else registry_default(key)


def resolve_ad_chapter_config(db, podcast_row, slug=None) -> AdChapterConfig:
    """Effective config for one feed; disabled when the feed writes no chapters."""
    if resolve_chapters_mode(podcast_row) == CHAPTERS_MODE_OFF:
        return AdChapterConfig.disabled()
    if not db.get_setting_bool('chapters_enabled', True):
        return AdChapterConfig.disabled()
    if not resolve_ad_chapters_enabled(db, podcast_row):
        return AdChapterConfig.disabled()
    try:
        min_confidence = float(_setting(db, 'ad_chapter_min_confidence'))
    except (TypeError, ValueError):
        min_confidence = float(registry_default('ad_chapter_min_confidence'))
    return AdChapterConfig(
        enabled=True,
        categories=db.resolve_ad_chapter_categories(slug, podcast_row),
        include_held=db.get_setting_bool('ad_chapters_include_held', False),
        title_format=_setting(db, 'ad_chapter_title_format'),
        held_title_format=_setting(db, 'ad_chapter_held_title_format'),
        resume_title=_setting(db, 'ad_chapter_resume_title'),
        min_confidence=min_confidence,
    )


def format_ad_chapter_title(fmt, category, default) -> str:
    """Render a title template; a template that cannot format falls back to default."""
    try:
        return fmt.format(category=category)
    except (KeyError, IndexError, ValueError, AttributeError):
        return default.format(category=category)


def strip_ad_chapters(chapters) -> list[dict]:
    return [ch for ch in (chapters or []) if ch.get('kind') not in AD_CHAPTER_KINDS]


def _marker_confidence(marker) -> float:
    for key in ('adjusted_confidence', 'confidence'):
        value = marker.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 1.0
    return 1.0


def _eligible_spans(markers, cuts, replacement_duration, config) -> list[dict]:
    spans = []
    for marker in markers:
        held = is_pending_review(marker)
        if held:
            if not config.include_held:
                continue
        elif marker.get('action_applied') != 'keep':
            continue
        category = normalize_segment_category(marker.get('category'))
        if not config.categories.get(category):
            continue
        if not held and _marker_confidence(marker) < config.min_confidence:
            continue
        start, end = marker.get('start'), marker.get('end')
        if start is None or end is None:
            continue
        start_s = max(1, int(round(adjust_timestamp(start, cuts, replacement_duration))))
        end_s = max(1, int(round(adjust_timestamp(end, cuts, replacement_duration))))
        if end_s <= start_s:
            continue
        spans.append({'start': start_s, 'end': end_s, 'category': category, 'held': held})
    spans.sort(key=lambda sp: sp['start'])
    # Overlapping spans would interleave ad/resume pairs; the first names the break.
    merged = []
    for span in spans:
        if merged and span['start'] <= merged[-1]['end']:
            merged[-1]['end'] = max(merged[-1]['end'], span['end'])
        else:
            merged.append(span)
    return merged


def merge_ad_chapters(chapters, markers, cuts, episode_duration,
                      replacement_duration, config) -> list[dict]:
    """Rebuild ad chapters from markers over the topic chapters in `chapters`.

    Stale ad entries are always stripped first, so the result is idempotent.
    Chapters have no end time, so each break gets a resume chapter at its end
    unless a topic chapter already starts within AD_CHAPTER_SNAP_SECONDS.
    """
    topics = strip_ad_chapters(chapters)
    if not config.enabled or not markers:
        return topics
    spans = _eligible_spans(markers, cuts or [], replacement_duration, config)
    if not spans:
        return topics

    def displaced(chapter):
        # A topic inside a break is gone; one at its start would shadow the ad entry.
        start = chapter['startTime']
        return any(sp['start'] < start < sp['end']
                   or abs(start - sp['start']) <= AD_CHAPTER_SNAP_SECONDS
                   for sp in spans)

    kept = [ch for ch in topics if not displaced(ch)]

    def snaps_to(time_s):
        return any(abs(ch['startTime'] - time_s) <= AD_CHAPTER_SNAP_SECONDS for ch in kept)

    end_s = int(round(episode_duration)) if episode_duration else None
    additions = []
    for span in spans:
        fmt = config.held_title_format if span['held'] else config.title_format
        default = registry_default(
            'ad_chapter_held_title_format' if span['held'] else 'ad_chapter_title_format')
        entry = {'startTime': span['start'],
                 'title': format_ad_chapter_title(fmt, span['category'], default),
                 'kind': 'ad', 'category': span['category']}
        if span['held']:
            entry['held'] = True
        additions.append(entry)
        if (end_s is None or span['end'] < end_s) and not snaps_to(span['end']):
            additions.append({'startTime': span['end'], 'title': config.resume_title,
                              'kind': 'resume'})
    return sorted(kept + additions, key=lambda ch: ch['startTime'])
