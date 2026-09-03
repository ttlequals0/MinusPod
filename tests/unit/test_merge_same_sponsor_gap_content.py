"""Same-sponsor merging must not swallow show content.

Sharing a sponsor used to be enough on its own when two detections sat within
SHORT_GAP_THRESHOLD of each other. On shows where the host name-drops a
sponsor through the episode, that merged across the conversation between the
two mentions. Two detections of 1.9s and 2.6s, 83s apart,
became one 88s span that was 95 percent show content.

The gap now has to be filler, measured the same way
merge_ads_across_short_content_gaps measures it, unless the gap itself still
talks about the sponsor.
"""
import os
import sys
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='merge_sponsor_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import merge_same_sponsor_ads


def _ad(start, end, reason):
    return {'start': start, 'end': end, 'confidence': 0.9, 'reason': reason}


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


# The sponsor is harvested from the transcript inside each ad's own range, so
# every fixture gives the two ads segments carrying the URL.
READ = 'Get it at acme.com today'
CHATTER = 'So anyway the listener wrote in about their drive and we talked it over.'


def _episode(ad1, ad2, gap_text=None, gap_span=None):
    """Segments for two sponsor reads, optionally with speech in the gap."""
    segs = [_seg(*ad1, READ), _seg(*ad2, READ)]
    if gap_text is not None:
        segs.append(_seg(*(gap_span or (ad1[1], ad2[0])), gap_text))
    return sorted(segs, key=lambda s: s['start'])


class TestGapContentGatesTheMerge:

    def test_filler_gap_still_merges(self):
        """Music or silence between two halves of one read leaves no speech
        in the gap, so it stays one ad."""
        ads = [_ad(100, 130, READ), _ad(150, 180, READ)]
        segments = _episode((100, 130), (150, 180))

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 1
        assert (merged[0]['start'], merged[0]['end']) == (100, 180)

    def test_speech_in_the_gap_keeps_them_separate(self):
        """Two brief mentions with the hosts talking in
        between. Previously merged on sponsor identity alone."""
        ads = [_ad(5447.4, 5449.3, READ), _ad(5532.5, 5535.1, READ)]
        segments = _episode((5447.4, 5449.3), (5532.5, 5535.1), gap_text=CHATTER)

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 2
        assert merged[0]['end'] == 5449.3
        assert merged[1]['start'] == 5532.5

    def test_sponsor_still_discussed_in_the_gap_merges(self):
        """A read that keeps going is one ad even across real speech."""
        ads = [_ad(100, 130, READ), _ad(200, 230, READ)]
        segments = _episode((100, 130), (200, 230),
                            gap_text='and again that is acme.com to order')

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 1
        assert (merged[0]['start'], merged[0]['end']) == (100, 230)

    def test_gap_speech_under_the_threshold_merges(self):
        """The discriminator is speech seconds, not wall clock: a 70s gap
        holding 10s of speech is still filler."""
        ads = [_ad(100, 130, READ), _ad(200, 230, READ)]
        segments = _episode((100, 130), (200, 230), gap_text=CHATTER,
                            gap_span=(130, 140))

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 1

    def test_zero_threshold_restores_the_old_behavior(self):
        """min_content_seconds <= 0 is the documented opt-out."""
        ads = [_ad(100, 130, READ), _ad(200, 230, READ)]
        segments = _episode((100, 130), (200, 230), gap_text=CHATTER)

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=0)

        assert len(merged) == 1


class TestAdCopyInTheGap:

    def test_a_read_split_around_its_own_call_to_action_merges(self):
        """The middle of one read: URL, code, and offer, but no sponsor name."""
        ads = [_ad(100, 130, READ), _ad(160, 190, READ)]
        segments = _episode((100, 130), (160, 190),
                            gap_text='Use promo code SHOW at checkout, that is '
                                     'dot com slash show for twenty percent off '
                                     'your first order and a free trial month')

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert [(a['start'], a['end']) for a in merged] == [(100, 190)]

    def test_conversation_with_a_stray_generic_phrase_stays_separate(self):
        ads = [_ad(100, 130, READ), _ad(160, 190, READ)]
        segments = _episode((100, 130), (160, 190),
                            gap_text='So anyway, go to the next one, the listener '
                                     'wrote in about their drive and we talked it over')

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 2


class TestZeroDurationAds:

    def test_a_zero_length_detection_never_extends_a_span(self):
        """A 33.8s read and a zero-length detection 71.7s
        later became one 105.5s span. The zero-length ad carries no audio, so
        merging with it can only push the end out across the gap."""
        ads = [_ad(5778.1, 5811.9, READ), _ad(5883.6, 5883.6, READ)]
        segments = _episode((5778.1, 5811.9), (5883.6, 5883.7), gap_text=CHATTER,
                            gap_span=(5811.9, 5883.6))

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert merged[0]['end'] == 5811.9

    def test_zero_length_survives_a_filler_gap_too(self):
        """Even with nothing in the gap it must not widen the span."""
        ads = [_ad(100, 130, READ), _ad(150, 150, READ)]
        segments = _episode((100, 130), (150, 150.1))

        merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

        assert len(merged) == 2
        assert merged[0]['end'] == 130


def test_two_short_reads_across_talk_stay_separate():
    """A 20.7s and a 7.8s detection
    85s apart became one 113.7s span."""
    ads = [_ad(5216.4, 5237.1, READ), _ad(5322.3, 5330.1, READ)]
    segments = _episode((5216.4, 5237.1), (5322.3, 5330.1), gap_text=CHATTER)

    merged = merge_same_sponsor_ads(ads, segments, min_content_seconds=12.0)

    assert len(merged) == 2
    total = sum(a['end'] - a['start'] for a in merged)
    assert total == pytest.approx(28.5, abs=0.1)
