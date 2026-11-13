"""Pattern audio fingerprints: store them from a retained original and carry them across a split or trim."""
import logging

from audio_fingerprinter import AudioFingerprinter, FingerprintStoreResult
from storage import PathContainmentError, Storage
from text_pattern_matcher import split_template_text
from utils.text import extract_timed_spans_in_range

logger = logging.getLogger('podcast.pattern_audio')


def resolve_retained_original(db, storage, slug, episode_id):
    """(path, None) for a retained original on disk, else (None, reason)."""
    episode = db.get_episode(slug, episode_id)
    if not episode or not episode.get('original_file'):
        return None, 'original_not_retained'
    try:
        path = storage.get_original_path(slug, episode_id)
    except PathContainmentError:
        return None, 'original_not_retained'
    return (path, None) if path.exists() else (None, 'original_not_retained')


class EpisodeAudio:
    """The retained original and the fingerprinter of one episode, each made on first use and shared by its patterns."""

    def __init__(self, db, slug, episode_id):
        self.db, self.slug, self.episode_id = db, slug, episode_id
        self._original = self._fingerprinter = None

    def original(self):
        if self._original is None:
            self._original = resolve_retained_original(self.db, Storage(), self.slug, self.episode_id)
        return self._original

    def fingerprinter(self):
        if self._fingerprinter is None:
            self._fingerprinter = AudioFingerprinter(db=self.db)
        return self._fingerprinter


def store_pattern_audio(db, slug, episode_id, pattern_id, start, end, *, force=False, episode_audio=None):
    """Fingerprint [start, end] of the retained original; force replaces a fingerprint and deleted audio."""
    episode_audio = episode_audio or EpisodeAudio(db, slug, episode_id)
    try:
        if not force and db.get_audio_fingerprint(pattern_id, with_blob=False) is not None:
            result = FingerprintStoreResult(False, 'fingerprint_exists')
        else:
            audio_path, err = episode_audio.original()
            if err:
                result = FingerprintStoreResult(False, 'original_not_retained')
            else:
                result = episode_audio.fingerprinter().store_fingerprint(
                    pattern_id, str(audio_path), start, end,
                    source_podcast_slug=slug, source_episode_id=episode_id, force=force)
    except Exception as e:
        logger.warning(f"Could not store audio for pattern {pattern_id}: {e}")
        result = FingerprintStoreResult(False, 'fingerprint_failed')
    if not result.stored:
        logger.debug(f"No fingerprint stored for pattern {pattern_id}: {result.skipped_reason}")
    return result


def _edge_time(spans, words, offsets, i, char_pos, fallback):
    """Time of char_pos inside segment i by linear interpolation, or fallback when it has no usable timing."""
    seg = spans[i]
    if seg['end'] <= seg['start'] or not words[i]:
        return fallback
    frac = min(max((char_pos - offsets[i]) / len(words[i]), 0.0), 1.0)
    return seg['start'] + frac * (seg['end'] - seg['start'])


def own_pattern_spans(db, slug, episode_id, pattern_ids, start, end, text, *, locate=False):
    """Each pattern's own (start, end) inside the marked span, None where it cannot be derived."""
    # locate: the templates were cut from the span by something other than split_template_text
    if not locate and (len(pattern_ids) < 2 or not text or len(split_template_text(text)) < 2):
        return {pid: (start, end) for pid in pattern_ids}
    spans = extract_timed_spans_in_range(
        db.get_transcript_for_timestamps(slug, episode_id) or '', start, end)
    words = [' '.join(span['text'].split()) for span in spans]
    joined = ' '.join(words)
    offsets = [sum(len(w) + 1 for w in words[:i]) for i in range(len(words))]
    found = {}
    patterns = db.get_ad_patterns_by_ids(pattern_ids)
    after = 0
    for pid in pattern_ids:
        pattern = patterns.get(pid) or {}
        template = ' '.join((pattern.get('text_template') or '').split())
        # split children come in text order, so a repeat inside an earlier child is not theirs
        lo = joined.find(template, after if locate else 0) if template else -1
        if locate and lo >= 0:
            after = lo + len(template)
        covered = [] if lo < 0 else [
            i for i, w in enumerate(words) if offsets[i] + len(w) > lo and offsets[i] < lo + len(template)]
        if covered:
            found[pid] = (lo, lo + len(template), covered[0], covered[-1])
    own = dict.fromkeys(pattern_ids)
    previous_last, previous_end = None, start
    for pid, (lo, hi, first, last) in sorted(found.items(), key=lambda item: item[1][:2]):
        if lo <= 0 and hi >= len(joined):
            own[pid] = (start, end)
        else:
            shared = previous_last == first
            # no usable timing on a shared segment: the first pattern keeps it whole, the next starts after it
            after = spans[first + 1]['start'] if first + 1 < len(spans) else spans[first]['end']
            begin_fallback = after if shared else spans[first]['start']
            begin = spans[first]['start'] if lo <= offsets[first] else _edge_time(
                spans, words, offsets, first, lo, begin_fallback)
            finish = spans[last]['end'] if hi >= offsets[last] + len(words[last]) else _edge_time(
                spans, words, offsets, last, hi, spans[last]['end'])
            begin, finish = max(start, begin), min(end, finish)
            own[pid] = (begin, finish) if finish > begin else None
        if own[pid] and own[pid][0] < previous_end:
            # overlapping templates: the earlier pattern keeps the shared time
            own[pid] = (previous_end, own[pid][1]) if own[pid][1] > previous_end else None
        if own[pid]:
            previous_end = own[pid][1]
        previous_last = last
    return own


def store_patterns_audio(db, slug, episode_id, pattern_ids, start, end, *, force_id=None, text=None, locate=False):
    """Fingerprint each pattern over its own span, forcing only force_id; returns [(pattern_id, result)]."""
    # text is what the patterns were cut from; a pattern of a split text with no derivable range stores nothing.
    try:
        own = own_pattern_spans(db, slug, episode_id, pattern_ids, start, end, text, locate=locate)
    except Exception as e:
        logger.warning(f"Could not derive the pattern spans of {slug}/{episode_id}: {e}")
        own = dict.fromkeys(pattern_ids)
    outcomes = []
    episode_audio = EpisodeAudio(db, slug, episode_id)
    for pid in pattern_ids:
        if own[pid] is None:
            outcomes.append((pid, FingerprintStoreResult(
                False, 'span_not_found' if locate else 'span_covers_multiple_patterns')))
        else:
            outcomes.append((pid, store_pattern_audio(
                db, slug, episode_id, pid, *own[pid], force=pid == force_id, episode_audio=episode_audio)))
    return outcomes


def audio_source_on(conn, pattern_id):
    """The source span of a pattern's usable fingerprint, or None when it has none, is off or was deleted."""
    row = conn.execute(
        "SELECT af.source_podcast_slug, af.source_episode_id, af.source_start, af.source_end "
        "FROM audio_fingerprints af JOIN ad_patterns ap ON ap.id = af.pattern_id "
        "WHERE af.pattern_id = ? AND af.enabled = 1 AND ap.audio_removed = 0", (pattern_id,)).fetchone()
    if not row or not row['source_podcast_slug'] or not row['source_episode_id']:
        return None
    start, end = row['source_start'], row['source_end']
    if start is None or end is None or end <= start:
        return None
    return {'slug': row['source_podcast_slug'], 'episode_id': row['source_episode_id'],
            'start': start, 'end': end}


def carry_audio_to_children(db, source, child_ids):
    """Fingerprint each still-active split child over its own span of the parent's source; best effort."""
    if not source:
        return []
    try:
        active = [pid for pid, p in db.get_ad_patterns_by_ids(child_ids).items() if p.get('is_active')]
        child_ids = [pid for pid in child_ids if pid in active]
        if not child_ids:
            return []
        return store_patterns_audio(
            db, source['slug'], source['episode_id'], child_ids, source['start'], source['end'], locate=True)
    except Exception as e:
        logger.warning(f"Could not carry audio to split patterns {child_ids}: {e}")
        return []


def rebuild_pattern_audio(db, source, pattern_id, expected_text, *, locate=True):
    """Force-replace a pattern's fingerprint from source if its audio is usable and its text is still expected_text."""
    if not source:
        return []
    try:
        pattern = db.get_ad_pattern_by_id(pattern_id)
        if (not pattern or pattern.get('text_template') != expected_text
                or audio_source_on(db.get_connection(), pattern_id) is None):
            return []
        if locate:
            return store_patterns_audio(
                db, source['slug'], source['episode_id'], [pattern_id], source['start'], source['end'],
                force_id=pattern_id, locate=True)
        return [(pattern_id, store_pattern_audio(
            db, source['slug'], source['episode_id'], pattern_id, source['start'], source['end'], force=True))]
    except Exception as e:
        logger.warning(f"Could not rebuild audio for pattern {pattern_id}: {e}")
        return []
