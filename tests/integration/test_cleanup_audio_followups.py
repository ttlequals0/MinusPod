"""Cleanup trims and splits find a source for the pattern's audio, move its duration and report the outcome."""
import logging

import pytest

from tests.integration.fingerprint_helpers import (  # noqa: E402
    EPISODE, FIRST_READ, SECOND_TEMPLATE, SLUG, TWO_SPONSORS, csrf, seed_timed_transcript,
    seed_two_sponsor_transcript,
)
import pattern_cleanup  # noqa: E402  must follow the helpers, which bootstrap the data dir
from pattern_audio import own_pattern_spans  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match  # noqa: E402

OLD = b'AQAAold'
OTHER = 'b2c3d4e5f6a1'
PIECES = [{'text': FIRST_READ, 'sponsor': 'Acme'}, {'text': SECOND_TEMPLATE, 'sponsor': 'Beta Corp'}]


def _stamp(sec):
    return f'{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:06.3f}'


def _seed_other_episode(db, lines, retained=True):
    from api import get_storage
    db.upsert_episode(slug=SLUG, episode_id=OTHER, original_url='https://example.com/b.mp3',
                      title='Episode Two', original_duration=300.0, status='processed')
    db.save_original_transcript(SLUG, OTHER, '\n'.join(
        f'[{_stamp(a)} --> {_stamp(b)}] {text}' for a, b, text in lines))
    if retained:
        path = get_storage().get_original_path(SLUG, OTHER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'ID3')
        db.upsert_episode(SLUG, OTHER, original_file=path.name)
        return path
    return None


def _legacy_pattern(db, avg_duration=None, created_from=EPISODE, text=TWO_SPONSORS):
    pid = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=text,
                               created_from_episode_id=created_from)
    db.create_audio_fingerprint(pid, OLD, 60.0)
    if avg_duration is not None:
        db.update_ad_pattern(pid, avg_duration=avg_duration)
    return pid


def _suggest(db, pid, kind, payload):
    before = pattern_cleanup._before_snapshot(db.get_ad_pattern_by_id(pid))
    return db.upsert_cleanup_suggestion(None, pid, kind, 0.9, ['r'], payload, before)


def _span(db, pid):
    row = db.get_audio_fingerprint(pid, with_blob=False)
    return row and (row['source_podcast_slug'], row['source_episode_id'], row['source_start'], row['source_end'])


def _trim(db, pid, text=SECOND_TEMPLATE):
    sid = _suggest(db, pid, 'trim', {'text': text})
    return sid, pattern_cleanup.apply_suggestion(db, sid)


def _split(db, pid):
    sid = _suggest(db, pid, 'split', {'pieces': PIECES})
    result = pattern_cleanup.apply_suggestion(db, sid)
    return sid, result, result['applied']['new_pattern_ids']


# A: the creating episode is the fallback source

def test_trim_of_a_legacy_fingerprint_rebuilds_from_the_creating_episode(
        fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    _, result = _trim(fp_feed, pid)
    assert _span(fp_feed, pid) == (SLUG, EPISODE, 40.0, 70.0)
    assert [o[1].stored for o in result['fingerprint_outcomes']] == [True]
    assert 'audio_unresolved' not in result['applied']


def test_legacy_trim_stays_unresolved_when_the_text_cannot_be_derived(
        fp_feed, fp_original, stub_fingerprinting):
    seed_timed_transcript(fp_feed, [(10.0, 70.0, 'Nothing like the pattern text appears in this episode at all.')])
    pid = _legacy_pattern(fp_feed)
    _, result = _trim(fp_feed, pid)
    assert {o[1].skipped_reason for o in result['fingerprint_outcomes']} == {'original_not_retained'}
    assert fp_feed.get_audio_fingerprint(pid)['fingerprint'] == OLD


def test_legacy_trim_stays_unresolved_when_the_creating_episode_is_not_retained(
        fp_feed, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    _, result = _trim(fp_feed, pid)
    assert {o[1].skipped_reason for o in result['fingerprint_outcomes']} == {'original_not_retained'}


def test_creating_episode_marker_gives_the_span_when_the_template_is_not_in_the_transcript(
        fp_feed, fp_original, stub_fingerprinting):
    filler = ' '.join(['Meanwhile the hosts talk about gardening and the weather in great detail.'] * 6)
    seed_timed_transcript(fp_feed, [(10.0, 40.0, FIRST_READ), (40.0, 70.0, filler),
                                    (70.0, 100.0, SECOND_TEMPLATE)])
    pid = _legacy_pattern(fp_feed)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[
        {'start': 10.0, 'end': 100.0, 'pattern_id': pid, 'was_cut': True,
         'validation': {'decision': 'ACCEPT'}}])
    _trim(fp_feed, pid)
    assert _span(fp_feed, pid) == (SLUG, EPISODE, 70.0, 100.0)


# B: source order and tolerant child location

def test_trim_prefers_the_creating_episode_where_the_text_can_be_located(
        fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    _seed_other_episode(fp_feed, [(10.0, 60.0, 'A different read that shares nothing with the template.')])
    pid = _legacy_pattern(fp_feed)
    fp_feed.create_audio_fingerprint(pid, OLD, 50.0, source_podcast_slug=SLUG, source_episode_id=OTHER,
                                     source_start=10.0, source_end=60.0)
    _trim(fp_feed, pid)
    assert _span(fp_feed, pid) == (SLUG, EPISODE, 40.0, 70.0)


def test_trim_keeps_the_recorded_source_when_the_creating_episode_lacks_the_text(
        fp_feed, fp_original, stub_fingerprinting):
    seed_timed_transcript(fp_feed, [(10.0, 70.0, 'Nothing like the pattern text appears in this episode at all.')])
    _seed_other_episode(fp_feed, [(10.0, 40.0, FIRST_READ), (40.0, 70.0, SECOND_TEMPLATE)])
    pid = _legacy_pattern(fp_feed)
    fp_feed.create_audio_fingerprint(pid, OLD, 60.0, source_podcast_slug=SLUG, source_episode_id=OTHER,
                                     source_start=10.0, source_end=70.0)
    _trim(fp_feed, pid)
    assert _span(fp_feed, pid) == (SLUG, OTHER, 40.0, 70.0)


def test_split_of_a_match_in_another_episode_finds_children_despite_wording_changes(
        fp_feed, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    first = FIRST_READ.replace('the easy way', 'the simple way').replace('today', 'right now')
    second = SECOND_TEMPLATE.replace('better way', 'best way').replace('bonus offer today', 'bonus offer')
    _seed_other_episode(fp_feed, [(100.0, 125.0, first), (125.0, 150.0, second)])
    pid = _legacy_pattern(fp_feed)
    record_fingerprint_match(fp_feed, pid, OTHER, SLUG, 100.0, 150.0, 0.9, 0.9, True, False)
    _, _, kids = _split(fp_feed, pid)
    spans = [_span(fp_feed, k) for k in kids]
    assert all(s is not None and s[:2] == (SLUG, OTHER) for s in spans)
    assert spans[0][2] == pytest.approx(100.0, abs=3) and spans[1][3] == pytest.approx(150.0, abs=3)
    assert spans[0][3] <= spans[1][2]


class _FakeDb:
    def __init__(self, transcript, templates):
        self._transcript, self._templates = transcript, templates

    def get_transcript_for_timestamps(self, slug, episode_id):
        return self._transcript

    def get_ad_patterns_by_ids(self, pattern_ids):
        return {pid: {'text_template': self._templates[pid]} for pid in pattern_ids}


def _located(lines, templates, start, end):
    transcript = '\n'.join(f'[{_stamp(a)} --> {_stamp(b)}] {text}' for a, b, text in lines)
    return own_pattern_spans(_FakeDb(transcript, templates), SLUG, EPISODE, list(templates), start, end,
                             None, locate=True)


def test_a_child_whose_text_is_not_in_the_span_still_gets_none():
    own = _located([(10.0, 40.0, FIRST_READ.replace('easy', 'quick')), (40.0, 70.0, 'Completely unrelated chatter here.')],
                   {1: FIRST_READ, 2: SECOND_TEMPLATE}, 10.0, 70.0)
    assert own[1] == pytest.approx((10.0, 40.0), abs=1.0) and own[2] is None


def test_fuzzy_children_stay_in_order_and_inside_the_span():
    lines = [(10.0, 40.0, FIRST_READ.replace('easy', 'quick')),
             (40.0, 70.0, SECOND_TEMPLATE.replace('better', 'finer'))]
    own = _located(lines, {1: FIRST_READ, 2: SECOND_TEMPLATE}, 20.0, 60.0)
    spans = [own[1], own[2]]
    assert all(s is None or (20.0 <= s[0] < s[1] <= 60.0) for s in spans)
    assert own[1] is not None and own[2] is not None and own[1][1] <= own[2][0]


def test_a_repeat_before_the_previous_child_is_not_taken_by_a_fuzzy_match():
    lines = [(10.0, 40.0, SECOND_TEMPLATE.replace('better', 'finer')), (40.0, 70.0, FIRST_READ)]
    own = _located(lines, {1: FIRST_READ, 2: SECOND_TEMPLATE}, 10.0, 70.0)
    assert own[1] == (40.0, 70.0) and own[2] is None


# C: a trim moves the stored duration

def _avg(db, pid):
    return db.get_ad_pattern_by_id(pid)['avg_duration']


def test_trim_rebuilt_audio_sets_the_duration_to_the_new_span(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed, avg_duration=92.0)
    _trim(fp_feed, pid)
    assert _avg(fp_feed, pid) == pytest.approx(30.0)


def test_trim_without_rebuilt_audio_scales_the_duration_by_text_length(fp_feed, stub_fingerprinting):
    pid = _legacy_pattern(fp_feed, avg_duration=92.0)
    _trim(fp_feed, pid)
    assert _avg(fp_feed, pid) == pytest.approx(92.0 * len(SECOND_TEMPLATE) / len(TWO_SPONSORS))


def test_trim_never_raises_the_duration_and_leaves_an_unset_one_unset(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    short = _legacy_pattern(fp_feed, avg_duration=12.0)
    _trim(fp_feed, short)
    unset = _legacy_pattern(fp_feed)
    _trim(fp_feed, unset)
    assert _avg(fp_feed, short) == pytest.approx(12.0) and _avg(fp_feed, unset) is None


def test_undo_trim_restores_the_exact_previous_duration(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed, avg_duration=92.5)
    sid, _ = _trim(fp_feed, pid)
    assert _avg(fp_feed, pid) != 92.5
    pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert _avg(fp_feed, pid) == 92.5


def test_undo_trim_is_not_refused_after_the_duration_moved_again(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed, avg_duration=92.5)
    sid, _ = _trim(fp_feed, pid)
    fp_feed.update_ad_pattern(pid, avg_duration=31.0)
    assert pattern_cleanup.undo_suggestion(fp_feed, sid)['status'] == 'undone'
    assert _avg(fp_feed, pid) == 92.5


def test_a_split_leaves_the_parent_duration_alone(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed, avg_duration=92.0)
    _split(fp_feed, pid)
    assert _avg(fp_feed, pid) == 92.0


# D: outcomes are logged and stored

def test_approve_logs_one_line_and_stores_the_outcomes(fp_feed, fp_original, stub_fingerprinting, caplog):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    with caplog.at_level(logging.INFO, logger='podcast.pattern_cleanup'):
        sid, result = _trim(fp_feed, pid)
    lines = [r.getMessage() for r in caplog.records if f'suggestion {sid}' in r.getMessage()]
    assert len(lines) == 1 and 'trim' in lines[0] and f'pattern {pid}' in lines[0] and 'stored' in lines[0]
    assert fp_feed.get_cleanup_suggestion(sid)['applied']['audio_outcomes'] == [
        {'pattern_id': pid, 'stored': True, 'reason': None}]


def test_unresolved_and_failed_outcomes_are_stored_with_their_reason(fp_feed, fp_original, stub_fingerprinting, caplog):
    seed_timed_transcript(fp_feed, [(10.0, 70.0, 'Nothing like the pattern text appears in this episode at all.')])
    pid = _legacy_pattern(fp_feed)
    with caplog.at_level(logging.INFO, logger='podcast.pattern_cleanup'):
        sid, _ = _trim(fp_feed, pid)
    assert fp_feed.get_cleanup_suggestion(sid)['applied']['audio_outcomes'] == [
        {'pattern_id': pid, 'stored': False, 'reason': 'original_not_retained'}]
    assert any('original_not_retained' in r.getMessage() for r in caplog.records)


def test_split_outcomes_name_every_child(fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    sid, _, kids = _split(fp_feed, pid)
    stored = fp_feed.get_cleanup_suggestion(sid)['applied']['audio_outcomes']
    assert [o['pattern_id'] for o in stored] == kids and all(o['stored'] for o in stored)


def test_listed_suggestions_return_the_stored_outcomes(app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    sid = _suggest(fp_feed, pid, 'trim', {'text': SECOND_TEMPLATE})
    app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve', headers=csrf(app_client))
    body = app_client.get('/api/v1/patterns/cleanup/suggestions?status=approved').get_json()
    item = next(s for s in body['suggestions'] if s['id'] == sid)
    assert item['applied']['audioOutcomes'] == [{'patternId': pid, 'stored': True, 'reason': None}]


def test_undo_logs_and_stores_its_outcome(fp_feed, fp_original, stub_fingerprinting, caplog):
    seed_two_sponsor_transcript(fp_feed)
    pid = _legacy_pattern(fp_feed)
    sid, _ = _trim(fp_feed, pid)
    with caplog.at_level(logging.INFO, logger='podcast.pattern_cleanup'):
        pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert any(f'suggestion {sid}' in r.getMessage() and 'undo' in r.getMessage() for r in caplog.records)
    assert 'undo_audio_outcomes' in fp_feed.get_cleanup_suggestion(sid)['applied']
