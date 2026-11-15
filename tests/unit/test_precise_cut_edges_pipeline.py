"""precise_cut_edges in the pipeline: the reviewer edge check request, the cross-pass render, stored spans."""
import json

from tests.app_bootstrap import bootstrap

bootstrap('precise_cut_edges_pipeline_test_')

import pytest

from main_app.pipeline import cuts, recut, review, verify
from tests.unit.reviewer_test_utils import _build_reviewer, _resp, _worded

SETTINGS = {'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
            'review_max_boundary_shift': '60'}


def _review_request(monkeypatch, flag, pass_num):
    """The model request the pipeline's reviewer sends for one accepted ad."""
    reviewer = _build_reviewer(SETTINGS)
    ad = {'start': 100.0, 'end': 160.0, 'confidence': 0.95}
    reviewer._llm_client.messages_create.return_value = _resp(json.dumps(
        [{'is_ad': True, 'start': 100.0, 'end': 160.0, 'confidence': 0.95}]))
    monkeypatch.setattr(review, 'resolve_precise_cut_edges', lambda db: flag)
    monkeypatch.setattr(review, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(review, '_build_reviewer', lambda db, detector: reviewer)
    monkeypatch.setattr(review.common, '_publish_status', lambda *a, **k: None)
    segments = [_worded(95.0, 99.0, 'And that is the end of our story for today.'),
                _worded(100.0, 160.0, 'This episode is brought to you by Acme. Go to acme.com today. ' * 6),
                _worded(160.5, 163.5, 'Welcome back to the conversation about gardens.')]
    review._run_ad_reviewer('slug', 'ep', 1, [ad], [ad], segments, 'Show', 'Episode', '', '', 0.8,
                            pass_num, 'model')
    return ' '.join(json.dumps(c.kwargs, default=str) for c in reviewer._llm_client.messages_create.call_args_list)


@pytest.mark.parametrize('flag,pass_num,asked', [
    (False, 1, False), (True, 1, True), (True, 2, False)])
def test_reviewer_asks_for_lead_in_and_tail_only_on_pass_1_with_the_flag_on(
        monkeypatch, flag, pass_num, asked):
    request = _review_request(monkeypatch, flag, pass_num)
    assert ('EDGE CHECK' in request) is asked


def test_cross_pass_render_places_edges_in_the_analyzer_spans(monkeypatch):
    monkeypatch.setattr(cuts, 'resolve_precise_cut_edges', lambda db: True)
    words = [{'word': 'one.', 'start': 99.0, 'end': 99.4},
             {'word': 'two', 'start': 100.6, 'end': 101.2},
             {'word': 'three', 'start': 101.3, 'end': 101.8}]
    segments = [{'start': 99.0, 'end': 101.8, 'text': 'one. two three', 'words': words}]
    spans = [{'start': 99.45, 'end': 100.55, 'duration': 1.1}]
    rendered = []

    class Audio:
        def process_episode(self, path, segments, **kwargs):
            rendered.extend(segments)
            return None

    cut = {'start': 100.6, 'end': 160.0, 'action_applied': 'remove'}
    verify._rerender_crosspass_from_original('slug', 'ep', 'missing.mp3', 'pass1.mp3', [cut], Audio(), [],
                                             segments=segments, silence_spans=spans)
    assert 99.45 < rendered[0]['start'] < 100.55


def test_reviewer_text_is_stored_as_the_model_wrote_it():
    from ad_reviewer import ReviewVerdict, stamp_reviewer_fields
    text = 'Sentence B1 is a self_promo lead-in found in pass 1.'
    ad = {'start': 1.0, 'end': 30.0}
    stamp_reviewer_fields(ad, ReviewVerdict(pool='accepted', pass_num=1, verdict='confirmed',
                                            original_start=1.0, original_end=30.0, reasoning=text))
    assert ad['reviewer_reasoning'] == text


def test_recut_reads_the_stored_silence_spans(monkeypatch):
    spans = [{'start': 99.2, 'end': 99.8, 'duration': 0.6}]
    stored = {'ep': json.dumps({'signals': [], 'silence_spans': spans}), 'none': None, 'bad': '{'}
    monkeypatch.setattr(recut.db, 'get_episode_audio_analysis', lambda slug, ep: stored[ep])
    assert recut._stored_silence_spans('slug', 'ep') == spans
    assert recut._stored_silence_spans('slug', 'none') is None
    assert recut._stored_silence_spans('slug', 'bad') is None
