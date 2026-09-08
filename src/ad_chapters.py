"""Ad chapters: segments left in the served audio published as their own
Podcasting 2.0 chapters so a chapter-aware player can skip them."""
from dataclasses import dataclass, field

from config import (
    AD_CHAPTER_KINDS, AD_CHAPTER_SNAP_SECONDS, CHAPTERS_MODE_OFF,
    DEFAULT_AD_CHAPTER_CATEGORIES, SEGMENT_CATEGORIES, is_pending_review,
    resolve_ad_chapters_enabled, resolve_chapters_mode,
)
from database.settings import registry_current_value, registry_default
from utils.time import adjust_timestamp

# Keys the merge adds for its own bookkeeping; never served or embedded.
INTERNAL_CHAPTER_KEYS = ('kind', 'category', 'held', 'hidden')


@dataclass(frozen=True)
class AdChapterConfig:
    enabled: bool = False
    categories: dict = field(
        default_factory=lambda: dict(DEFAULT_AD_CHAPTER_CATEGORIES))
    include_held: bool = False
    title_format: str = registry_default('ad_chapter_title_format')
    held_title_format: str = registry_default('ad_chapter_held_title_format')
    resume_title: str = registry_default('ad_chapter_resume_title')
    min_confidence: float = float(registry_default('ad_chapter_min_confidence'))

    @classmethod
    def disabled(cls) -> 'AdChapterConfig':
        return cls(enabled=False)

    def held_status(self, marker) -> bool | None:
        """None when the marker gets no chapter, else True if it is a held one."""
        # A keep marker is eligible on its own, even if it also carries a hold.
        is_keep = marker.get('action_applied') == 'keep'
        held = not is_keep and is_pending_review(marker)
        if not is_keep and not held:
            return None
        if held and not self.include_held:
            return None
        # Raw category: an unset or unknown one is not a sponsor read, so it
        # gets no chapter at all.
        category = marker.get('category')
        if category not in SEGMENT_CATEGORIES or not self.categories.get(category):
            return None
        if not held and _marker_confidence(marker) < self.min_confidence:
            return None
        return held


def resolve_ad_chapter_config(db, podcast_row, slug=None) -> AdChapterConfig:
    """Effective config for one feed; disabled when the feed writes no chapters."""
    if resolve_chapters_mode(podcast_row) == CHAPTERS_MODE_OFF:
        return AdChapterConfig.disabled()
    if not db.get_setting_bool('chapters_enabled', True):
        return AdChapterConfig.disabled()
    if not resolve_ad_chapters_enabled(db, podcast_row):
        return AdChapterConfig.disabled()
    return AdChapterConfig(
        enabled=True,
        categories=db.resolve_ad_chapter_categories(slug, podcast_row),
        include_held=db.get_setting_bool('ad_chapters_include_held', False),
        title_format=registry_current_value(db, 'ad_chapter_title_format'),
        held_title_format=registry_current_value(db, 'ad_chapter_held_title_format'),
        resume_title=registry_current_value(db, 'ad_chapter_resume_title'),
        min_confidence=float(registry_current_value(db, 'ad_chapter_min_confidence')),
    )


def format_ad_chapter_title(fmt, category) -> str:
    return fmt.format(category=category)


def strip_ad_chapters(chapters) -> list[dict]:
    """Topic chapters only, each unhidden: a rebuild re-decides what is displaced."""
    return [{k: v for k, v in ch.items() if k != 'hidden'}
            for ch in (chapters or []) if ch.get('kind') not in AD_CHAPTER_KINDS]


def public_chapters(entries) -> list[dict]:
    """Spec-clean chapters for serving and embedding: hidden entries and the
    internal bookkeeping keys dropped."""
    return [{k: v for k, v in ch.items() if k not in INTERNAL_CHAPTER_KEYS}
            for ch in (entries or []) if not ch.get('hidden')]


def _marker_confidence(marker) -> float:
    # The validator writes its adjusted score under 'validation'; the
    # top-level key is the older shape.
    for value in ((marker.get('validation') or {}).get('adjusted_confidence'),
                  marker.get('adjusted_confidence'), marker.get('confidence')):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 1.0


def _eligible_spans(markers, cuts, replacement_duration, config) -> list[dict]:
    spans = []
    for marker in markers:
        held = config.held_status(marker)
        if held is None:
            continue
        start, end = marker.get('start'), marker.get('end')
        if start is None or end is None:
            continue
        start_s = max(1, int(round(adjust_timestamp(start, cuts, replacement_duration))))
        end_s = max(1, int(round(adjust_timestamp(end, cuts, replacement_duration))))
        if end_s <= start_s:
            continue
        spans.append({'start': start_s, 'end': end_s, 'held': held,
                      'category': marker['category']})
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
                      replacement_duration, config=None) -> list[dict]:
    """Topic chapters plus ad chapters rebuilt from markers; idempotent, and a
    displaced topic chapter is hidden rather than deleted so a later rebuild
    can restore it."""
    topics = strip_ad_chapters(chapters)
    if config is None or not config.enabled or not markers:
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

    kept = [{**ch, 'hidden': True} if displaced(ch) else ch for ch in topics]
    visible = [ch for ch in kept if not ch.get('hidden')]

    def snaps_to(time_s):
        return any(abs(ch['startTime'] - time_s) <= AD_CHAPTER_SNAP_SECONDS
                   for ch in visible)

    end_s = int(round(episode_duration)) if episode_duration else None
    additions = []
    for span in spans:
        fmt = config.held_title_format if span['held'] else config.title_format
        entry = {'startTime': span['start'],
                 'title': format_ad_chapter_title(fmt, span['category']),
                 'kind': 'ad', 'category': span['category']}
        if span['held']:
            entry['held'] = True
        additions.append(entry)
        if (end_s is None or span['end'] < end_s) and not snaps_to(span['end']):
            additions.append({'startTime': span['end'], 'title': config.resume_title,
                              'kind': 'resume'})
    return sorted(kept + additions, key=lambda ch: ch['startTime'])
