"""Confirms _load_cue_config uses resolve_cue_template_score (Phase 5).

The resolver returns the per-feed override when set, and the test verifies the
matcher is instantiated with that value rather than the raw global setting.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-wire5-'))


class _DB:
    def __init__(self, override=None, global_score=0.75):
        self._override = override
        self._global = global_score
        self._bools = {'audio_cue_detection_enabled': True}

    def get_setting_bool(self, key, default=False):
        return self._bools.get(key, default)

    def get_setting(self, key):
        return None

    def get_setting_float(self, key, default=0.0):
        if key == 'audio_cue_template_score':
            return self._global
        return default

    def get_podcast_cue_score_override(self, podcast_id):
        return self._override

    def list_active_cue_templates_for_feed(self, feed_id):
        # Return a fake template row; actual content is irrelevant because
        # AudioCueTemplateMatcher is replaced by _StubMatcher.
        return [{'id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
                 'duration_s': 0.5, 'sample_rate': 16000, 'n_coeffs': 13,
                 'mfcc_blob': b'\x00' * 104, 'pcm_blob': None}]


_captured_threshold = []


class _StubMatcher:
    is_usable = True

    def __init__(self, templates, score_threshold=0.75, **kw):
        _captured_threshold.clear()
        _captured_threshold.append(score_threshold)


def test_load_cue_config_uses_override(monkeypatch):
    import audio_analysis.audio_analyzer as aa
    monkeypatch.setattr(aa, 'AudioCueTemplateMatcher', _StubMatcher)

    from audio_analysis.audio_analyzer import AudioAnalyzer
    analyzer = AudioAnalyzer(db=_DB(override=0.65, global_score=0.75))
    analyzer._load_cue_config(feed_id=1)
    assert _captured_threshold == [0.65]


def test_load_cue_config_falls_back_to_global(monkeypatch):
    import audio_analysis.audio_analyzer as aa
    monkeypatch.setattr(aa, 'AudioCueTemplateMatcher', _StubMatcher)

    from audio_analysis.audio_analyzer import AudioAnalyzer
    analyzer = AudioAnalyzer(db=_DB(override=None, global_score=0.80))
    analyzer._load_cue_config(feed_id=1)
    assert _captured_threshold == [0.80]
