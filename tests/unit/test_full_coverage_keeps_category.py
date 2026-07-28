"""A keep-resolving category must survive a fully covering pattern match.

Observed on a feed with detect_show_segments=true and per-feed actions
{cross_promo,intro,outro,recap,self_promo: keep; sponsor,interaction: remove}.

A second, independent layer from the window-dedup fix covered in
test_ad_detector.py::TestDeduplicateWindowAdsActionGate and
test_segment_categories.py::TestIntroOutroSurviveFullPipeline:
process_transcript's pattern-merge step silently discarded a Claude ad's
category whenever its span was fully covered by an existing
fingerprint/text_pattern match. An uncategorized pattern was all that
remained, losing a keep-resolving 'intro'/'outro' detection even when the
window-boundary dedup left it intact.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector
from text_pattern_matcher import TextMatch

SEGMENTS = [
    {'start': 0.0, 'end': 166.6, 'text': 'sponsor reads and then the show intro plays'},
    {'start': 166.6, 'end': 200.0, 'text': 'welcome back to the show with more news content'},
]

KEEP_SEGMENTS_ACTION_MAP = {
    'sponsor': 'remove', 'interaction': 'remove',
    'cross_promo': 'keep', 'self_promo': 'keep',
    'intro': 'keep', 'outro': 'keep', 'recap': 'keep',
}


class _FakeDb:
    def __init__(self, action_map):
        self._action_map = action_map

    def get_false_positive_corrections(self, episode_id):
        return []

    def get_podcast_false_positive_texts(self, slug):
        return []

    def get_setting(self, key):
        return None

    def get_setting_float(self, key, default):
        return default

    def resolve_segment_actions(self, slug):
        return self._action_map


class _LegacyPatternMatcher:
    """Stand-in for text_pattern_matcher: one legacy (uncategorized)
    pattern spanning 0.0-166.6s, exactly matching the real incident's
    fingerprint/text-pattern coverage of the fused pre-roll+intro region."""

    def is_available(self):
        return True

    def find_matches(self, *args, **kwargs):
        return [TextMatch(
            pattern_id=501, start=0.0, end=166.6, confidence=0.95,
            sponsor=None, match_type='content', category=None,
        )]


def _run(detector, claude_ads):
    detector.db = _FakeDb(KEEP_SEGMENTS_ACTION_MAP)
    detector.audio_fingerprinter = None
    detector.text_pattern_matcher = _LegacyPatternMatcher()
    detector.pattern_service = None

    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': claude_ads, 'status': 'success'}):
        return detector.process_transcript(
            SEGMENTS, 'Example Podcast', 'Episode One',
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            podcast_id='example-podcast', skip_patterns=False,
            audio_path=None, dai_differential=None, keep_content=False,
        )


def test_intro_fully_covered_by_legacy_pattern_still_survives_as_keep():
    detector = AdDetector(api_key='test-key')
    intro_ad = {'start': 158.0, 'end': 166.6, 'confidence': 0.9,
                'category': 'intro', 'reason': 'Show intro marker/theme'}

    result = _run(detector, [intro_ad])
    ads = result['ads']

    by_cat = {a.get('category'): a for a in ads}
    assert 'intro' in by_cat, (
        f"intro category lost when fully covered by an uncategorized "
        f"pattern match; got {ads}"
    )
    assert by_cat['intro']['start'] == 158.0
    assert by_cat['intro']['end'] == 166.6

    # The legacy pattern's own remove-resolving span still covers the
    # pre-roll audio ahead of the intro, so nothing is left uncut. It has no
    # category of its own (the pattern predates the column) and is no longer
    # relabelled 'sponsor' to hide that.
    assert None in by_cat
    assert by_cat[None]['start'] == 0.0
    assert by_cat[None]['end'] == 158.0


def test_default_action_map_still_drops_fully_covered_claude_ad():
    """Regression: a plain sponsor-categorized (or uncategorized) Claude ad
    that is fully covered by a pattern match is still dropped in favor of
    the pattern's own marker: the fix only protects a non-default
    (keep-resolving) category."""
    detector = AdDetector(api_key='test-key')
    sponsor_ad = {'start': 10.0, 'end': 150.0, 'confidence': 0.9,
                  'reason': 'Sponsor read'}

    result = _run(detector, [sponsor_ad])
    ads = result['ads']

    assert len(ads) == 1
    assert ads[0]['detection_stage'] == 'text_pattern'
    assert ads[0]['start'] == 0.0 and ads[0]['end'] == 166.6


def test_no_action_map_preserves_pre_fix_drop_behavior():
    """A feed/db with no resolvable action map (module-level callers, cold
    detector) must behave exactly as before this fix: the Claude ad's
    category cannot be resolved to a non-default action without a map, so
    a fully-covered ad is still dropped."""
    detector = AdDetector(api_key='test-key')
    detector.db = None
    detector.audio_fingerprinter = None
    detector.text_pattern_matcher = _LegacyPatternMatcher()
    detector.pattern_service = None
    intro_ad = {'start': 158.0, 'end': 166.6, 'confidence': 0.9,
                'category': 'intro', 'reason': 'Show intro marker/theme'}

    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [intro_ad], 'status': 'success'}):
        result = detector.process_transcript(
            SEGMENTS, 'Example Podcast', 'Episode One',
            slug=None, episode_id='a1b2c3d4e5f6',
            podcast_id=None, skip_patterns=False,
            audio_path=None, dai_differential=None, keep_content=False,
        )

    ads = result['ads']
    assert len(ads) == 1
    assert ads[0]['detection_stage'] == 'text_pattern'
    # The surviving pattern match carries no category of its own, and the
    # merge seam no longer invents one. Only the Claude ad's own (now-lost)
    # 'intro' category is gone.
    assert 'category' not in ads[0]
