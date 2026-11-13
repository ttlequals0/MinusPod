"""A split gives each child its own fingerprint and a trim rebuilds it, both from the parent's source episode."""
import pytest

from tests.integration.fingerprint_helpers import (  # noqa: E402
    EPISODE, FIRST_READ, SECOND_TEMPLATE, SLUG, TWO_SPONSORS, csrf, seed_timed_transcript,
    seed_two_sponsor_transcript,
)
import pattern_cleanup  # noqa: E402  must follow the helpers, which bootstrap the data dir

OLD = b'AQAAold'
PIECES = [{'text': FIRST_READ, 'sponsor': 'Acme'}, {'text': SECOND_TEMPLATE, 'sponsor': 'Beta Corp'}]


def _make_parent(fp_feed, lines=None):
    if lines:
        seed_timed_transcript(fp_feed, lines)
    else:
        seed_two_sponsor_transcript(fp_feed)
    pid = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TWO_SPONSORS)
    fp_feed.create_audio_fingerprint(pid, OLD, 60.0, source_podcast_slug=SLUG, source_episode_id=EPISODE,
                                     source_start=10.0, source_end=70.0)
    return pid


@pytest.fixture
def parent(fp_feed):
    return _make_parent(fp_feed)


def _suggest(db, pid, kind, payload):
    before = pattern_cleanup._before_snapshot(db.get_ad_pattern_by_id(pid))
    return db.upsert_cleanup_suggestion(None, pid, kind, 0.9, ['r'], payload, before)


def _span(db, pid):
    row = db.get_audio_fingerprint(pid, with_blob=False)
    return row and (row['source_start'], row['source_end'])


def _split(db, pid):
    sid = _suggest(db, pid, 'split', {'pieces': PIECES})
    result = pattern_cleanup.apply_suggestion(db, sid)
    return sid, result, result['applied']['new_pattern_ids']


def test_cleanup_split_gives_each_child_its_own_span(fp_feed, fp_original, stub_fingerprinting, parent):
    _, result, kids = _split(fp_feed, parent)
    assert [_span(fp_feed, k) for k in kids] == [(10.0, 40.0), (40.0, 70.0)]
    assert [o[1].stored for o in result['fingerprint_outcomes']] == [True, True]
    assert fp_feed.get_audio_fingerprint(parent, with_blob=False) is not None


def test_cleanup_split_without_original_still_succeeds(fp_feed, stub_fingerprinting, parent):
    _, result, kids = _split(fp_feed, parent)
    assert result['status'] == 'approved' and len(kids) == 2
    assert all(_span(fp_feed, k) is None for k in kids)
    assert {o[1].skipped_reason for o in result['fingerprint_outcomes']} == {'original_not_retained'}


def test_cleanup_split_with_parent_audio_off_gives_children_none(
        fp_feed, fp_original, stub_fingerprinting, parent):
    fp_feed.set_fingerprint_enabled(parent, False)
    _, _, kids = _split(fp_feed, parent)
    assert all(_span(fp_feed, k) is None for k in kids)


def test_cleanup_split_with_deleted_parent_audio_gives_children_none(
        fp_feed, fp_original, stub_fingerprinting, parent):
    fp_feed.set_pattern_audio_removed(parent, True)
    _, _, kids = _split(fp_feed, parent)
    assert all(_span(fp_feed, k) is None for k in kids)


def test_cleanup_split_child_not_in_the_transcript_gets_none(fp_feed, fp_original, stub_fingerprinting):
    parent = _make_parent(fp_feed, [(10.0, 40.0, FIRST_READ), (40.0, 70.0, 'Something else entirely.')])
    _, _, kids = _split(fp_feed, parent)
    assert _span(fp_feed, kids[0]) == (10.0, 40.0) and _span(fp_feed, kids[1]) is None


def test_fingerprint_failure_never_fails_the_cleanup_split(fp_feed, fp_original, parent, monkeypatch):
    from audio_fingerprinter import AudioFingerprinter

    def boom(self, *a, **k):
        raise RuntimeError('decoder crashed')
    monkeypatch.setattr(AudioFingerprinter, '_find_fpcalc', lambda self: '/usr/bin/fpcalc')
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint', boom)
    _, result, kids = _split(fp_feed, parent)
    assert result['status'] == 'approved' and len(kids) == 2
    assert all(_span(fp_feed, k) is None for k in kids)


def test_undo_split_reactivates_the_parent_with_its_audio(fp_feed, fp_original, stub_fingerprinting, parent):
    sid, _, kids = _split(fp_feed, parent)
    pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert fp_feed.get_ad_pattern_by_id(parent)['is_active']
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] == OLD
    assert not any(fp_feed.get_ad_pattern_by_id(k)['is_active'] for k in kids)


def test_manual_split_route_reports_and_stores_child_audio(
        app_client, fp_feed, fp_original, stub_fingerprinting, parent):
    resp = app_client.post(f'/api/v1/patterns/{parent}/split', headers=csrf(app_client))
    body = resp.get_json()
    assert resp.status_code == 200 and body['fingerprintStored'] is True
    assert [_span(fp_feed, k) for k in body['new_pattern_ids']] == [(10.0, 40.0), (40.0, 70.0)]


def test_manual_split_without_original_reports_the_reason(app_client, fp_feed, stub_fingerprinting, parent):
    body = app_client.post(f'/api/v1/patterns/{parent}/split', headers=csrf(app_client)).get_json()
    assert (body['success'], body['fingerprintStored'], body['fingerprintSkippedReason']) == (
        True, False, 'original_not_retained')


def test_manual_split_of_a_pattern_without_audio_has_no_fingerprint_fields(
        app_client, fp_feed, fp_original, stub_fingerprinting, parent):
    fp_feed.delete_audio_fingerprint(parent)
    body = app_client.post(f'/api/v1/patterns/{parent}/split', headers=csrf(app_client)).get_json()
    assert body['success'] and 'fingerprintStored' not in body


def test_cleanup_approve_route_reports_the_outcome(app_client, fp_feed, fp_original, stub_fingerprinting, parent):
    sid = _suggest(fp_feed, parent, 'split', {'pieces': PIECES})
    body = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve',
                           headers=csrf(app_client)).get_json()
    assert body['status'] == 'approved' and body['fingerprintStored'] is True


def _trim(db, pid, text=SECOND_TEMPLATE):
    sid = _suggest(db, pid, 'trim', {'text': text})
    return sid, pattern_cleanup.apply_suggestion(db, sid)


def test_trim_rebuilds_the_fingerprint_from_the_trimmed_span(fp_feed, fp_original, stub_fingerprinting, parent):
    _, result = _trim(fp_feed, parent)
    assert _span(fp_feed, parent) == (40.0, 70.0)
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] != OLD
    assert result['fingerprint_outcomes'][0][1].stored


def test_trim_that_cannot_be_located_keeps_the_old_fingerprint(fp_feed, fp_original, stub_fingerprinting):
    parent = _make_parent(fp_feed, [(10.0, 70.0, 'Nothing like the pattern text here.')])
    _trim(fp_feed, parent)
    assert _span(fp_feed, parent) == (10.0, 70.0)
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] == OLD


def test_trim_without_original_keeps_the_old_fingerprint(fp_feed, stub_fingerprinting, parent):
    _, result = _trim(fp_feed, parent)
    assert result['status'] == 'approved' and fp_feed.get_audio_fingerprint(parent)['fingerprint'] == OLD


def test_trim_with_audio_off_leaves_the_fingerprint(fp_feed, fp_original, stub_fingerprinting, parent):
    fp_feed.set_fingerprint_enabled(parent, False)
    _trim(fp_feed, parent)
    assert _span(fp_feed, parent) == (10.0, 70.0)


def test_undo_trim_restores_the_pre_trim_span(fp_feed, fp_original, stub_fingerprinting, parent):
    sid, _ = _trim(fp_feed, parent)
    pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert fp_feed.get_ad_pattern_by_id(parent)['text_template'] == TWO_SPONSORS
    assert _span(fp_feed, parent) == (10.0, 70.0)
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] != OLD


def test_undo_trim_after_a_kept_fingerprint_changes_nothing(fp_feed, stub_fingerprinting, parent):
    sid, _ = _trim(fp_feed, parent)
    pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] == OLD
