"""A split gives each child its own fingerprint and a trim rebuilds it, both from the parent's source episode."""
import pytest

from tests.integration.fingerprint_helpers import (  # noqa: E402
    EPISODE, FIRST_READ, SECOND_TEMPLATE, SLUG, TWO_SPONSORS, csrf, seed_timed_transcript,
    seed_two_sponsor_transcript,
)
import pattern_cleanup  # noqa: E402  must follow the helpers, which bootstrap the data dir
from tests.fingerprint_match_helpers import record_fingerprint_match  # noqa: E402

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


def _bulk(app_client, ids):
    return app_client.post('/api/v1/patterns/cleanup/suggestions/bulk', headers=csrf(app_client),
                           json={'ids': ids, 'action': 'approve'}).get_json()['results']


def _split_and_trim_suggestions(fp_feed):
    split_parent = _make_parent(fp_feed)
    trim_parent = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TWO_SPONSORS)
    fp_feed.create_audio_fingerprint(trim_parent, OLD, 60.0, source_podcast_slug=SLUG, source_episode_id=EPISODE,
                                     source_start=10.0, source_end=70.0)
    ids = [_suggest(fp_feed, trim_parent, 'trim', {'text': SECOND_TEMPLATE}),
           _suggest(fp_feed, split_parent, 'split', {'pieces': PIECES})]
    return trim_parent, ids


def test_bulk_approve_carries_audio_for_a_split_and_a_trim(app_client, fp_feed, fp_original, stub_fingerprinting):
    trim_parent, ids = _split_and_trim_suggestions(fp_feed)
    results = _bulk(app_client, ids)
    assert [r['fingerprintStored'] for r in results] == [True, True]
    kids = fp_feed.get_cleanup_suggestion(ids[1])['applied']['new_pattern_ids']
    assert [_span(fp_feed, k) for k in kids] == [(10.0, 40.0), (40.0, 70.0)]
    assert _span(fp_feed, trim_parent) == (40.0, 70.0)


def test_bulk_fingerprint_failure_in_one_item_does_not_fail_the_batch(
        app_client, fp_feed, fp_original, stub_fingerprinting, monkeypatch):
    from audio_fingerprinter import AudioFingerprinter
    trim_parent, ids = _split_and_trim_suggestions(fp_feed)
    real = AudioFingerprinter.generate_fingerprint
    calls = []

    def first_call_fails(self, path, start=0, duration=None):
        calls.append(start)
        if len(calls) == 1:
            raise RuntimeError('decoder crashed')
        return real(self, path, start, duration)
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint', first_call_fails)
    trim, split = _bulk(app_client, ids)
    assert trim['status'] == 'approved' and trim['fingerprintStored'] is False
    assert trim['fingerprintSkippedReason'] == 'fingerprint_failed'
    assert split['status'] == 'approved' and split['fingerprintStored'] is True
    assert fp_feed.get_audio_fingerprint(trim_parent)['fingerprint'] == OLD


def _between_commit_and_rebuild(monkeypatch, action):
    real = pattern_cleanup.rebuild_pattern_audio

    def rebuild(db, source, pattern_id, *args, **kwargs):
        action()
        return real(db, source, pattern_id, *args, **kwargs)
    monkeypatch.setattr(pattern_cleanup, 'rebuild_pattern_audio', rebuild)


def test_undo_between_trim_commit_and_rebuild_keeps_the_original_fingerprint(
        fp_feed, fp_original, stub_fingerprinting, parent, monkeypatch):
    sid = _suggest(fp_feed, parent, 'trim', {'text': SECOND_TEMPLATE})
    _between_commit_and_rebuild(monkeypatch, lambda: pattern_cleanup.undo_suggestion(fp_feed, sid))
    pattern_cleanup.apply_suggestion(fp_feed, sid)
    assert fp_feed.get_ad_pattern_by_id(parent)['text_template'] == TWO_SPONSORS
    assert fp_feed.get_audio_fingerprint(parent)['fingerprint'] == OLD


def test_audio_deleted_between_trim_commit_and_rebuild_is_not_recreated(
        fp_feed, fp_original, stub_fingerprinting, parent, monkeypatch):
    def delete_audio():
        fp_feed.delete_audio_fingerprint(parent)
        fp_feed.set_pattern_audio_removed(parent, True)
    _between_commit_and_rebuild(monkeypatch, delete_audio)
    _trim(fp_feed, parent)
    assert fp_feed.get_audio_fingerprint(parent, with_blob=False) is None


def test_child_deactivated_before_the_carry_gets_no_audio(
        fp_feed, fp_original, stub_fingerprinting, parent, monkeypatch):
    real = pattern_cleanup.carry_audio_to_children

    def carry(db, source, child_ids):
        db.update_ad_pattern(child_ids[0], is_active=0)
        return real(db, source, child_ids)
    monkeypatch.setattr(pattern_cleanup, 'carry_audio_to_children', carry)
    _, _, kids = _split(fp_feed, parent)
    assert _span(fp_feed, kids[0]) is None and _span(fp_feed, kids[1]) == (40.0, 70.0)


def test_undo_trim_after_the_original_expired_keeps_the_trimmed_fingerprint(
        fp_feed, fp_original, stub_fingerprinting, parent):
    sid, _ = _trim(fp_feed, parent)
    fp_original.unlink()
    result = pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert fp_feed.get_ad_pattern_by_id(parent)['text_template'] == TWO_SPONSORS
    assert _span(fp_feed, parent) == (40.0, 70.0)
    assert result['fingerprint_outcomes'][0][1].skipped_reason == 'original_not_retained'


def test_unlocatable_child_and_trim_report_span_not_found(fp_feed, fp_original, stub_fingerprinting):
    parent = _make_parent(fp_feed, [(10.0, 40.0, FIRST_READ), (40.0, 70.0, 'Something else entirely.')])
    _, result, _ = _split(fp_feed, parent)
    assert [o[1].skipped_reason for o in result['fingerprint_outcomes']] == [None, 'span_not_found']


def test_unlocatable_trim_reports_span_not_found(fp_feed, fp_original, stub_fingerprinting):
    parent = _make_parent(fp_feed, [(10.0, 70.0, 'Nothing like the pattern text here.')])
    _, result = _trim(fp_feed, parent)
    assert [o[1].skipped_reason for o in result['fingerprint_outcomes']] == ['span_not_found']


def test_child_text_repeated_inside_an_earlier_child_gets_its_own_later_span(
        fp_feed, fp_original, stub_fingerprinting):
    tail = 'Visit acme.com today.'
    parent = _make_parent(fp_feed, [(10.0, 40.0, FIRST_READ), (40.0, 70.0, tail)])
    sid = _suggest(fp_feed, parent, 'split', {'pieces': [
        {'text': FIRST_READ, 'sponsor': 'Acme'}, {'text': tail, 'sponsor': 'Acme Tax'}]})
    kids = pattern_cleanup.apply_suggestion(fp_feed, sid)['applied']['new_pattern_ids']
    assert [_span(fp_feed, k) for k in kids] == [(10.0, 40.0), (40.0, 70.0)]


def test_overlapping_child_text_gets_audio_only_for_the_earlier_child(
        fp_feed, fp_original, stub_fingerprinting, parent):
    sid = _suggest(fp_feed, parent, 'split', {'pieces': [
        {'text': FIRST_READ, 'sponsor': 'Acme'}, {'text': TWO_SPONSORS, 'sponsor': 'Beta Corp'}]})
    kids = pattern_cleanup.apply_suggestion(fp_feed, sid)['applied']['new_pattern_ids']
    assert _span(fp_feed, kids[0]) == (10.0, 40.0) and _span(fp_feed, kids[1]) is None


def test_parent_fingerprint_without_a_source_gives_children_no_audio_and_no_fields(
        app_client, fp_feed, fp_original, stub_fingerprinting, parent):
    fp_feed.create_audio_fingerprint(parent, OLD, 60.0)
    body = app_client.post(f'/api/v1/patterns/{parent}/split', headers=csrf(app_client)).get_json()
    assert body['success'] and 'fingerprintStored' not in body
    assert all(_span(fp_feed, k) is None for k in body['new_pattern_ids'])


def test_undone_split_children_are_not_loaded_by_the_matcher(fp_feed, fp_original, stub_fingerprinting, parent):
    sid, _, kids = _split(fp_feed, parent)
    loaded = {row['pattern_id'] for row in fp_feed.get_active_fingerprints_with_scope()}
    assert set(kids) <= loaded and parent not in loaded
    pattern_cleanup.undo_suggestion(fp_feed, sid)
    loaded = {row['pattern_id'] for row in fp_feed.get_active_fingerprints_with_scope()}
    assert parent in loaded and not set(kids) & loaded


@pytest.fixture
def legacy(fp_feed):
    """A parent whose fingerprint predates recorded sources."""
    pid = _make_parent(fp_feed)
    fp_feed.delete_audio_fingerprint(pid)
    fp_feed.create_audio_fingerprint(pid, OLD, 60.0)
    return pid


def _match(db, pid, in_scope=True, start=10.0, end=70.0):
    return record_fingerprint_match(db, pid, EPISODE, SLUG, start, end, 0.9, 0.9, in_scope, False)


def test_legacy_trim_rebuilds_from_a_retained_in_scope_match(fp_feed, fp_original, stub_fingerprinting, legacy):
    _match(fp_feed, legacy)
    _, result = _trim(fp_feed, legacy)
    assert _span(fp_feed, legacy) == (40.0, 70.0)
    assert result['fingerprint_outcomes'][0][1].stored


def test_legacy_split_gives_children_audio_from_a_retained_in_scope_match(
        fp_feed, fp_original, stub_fingerprinting, legacy):
    _match(fp_feed, legacy)
    _, result, kids = _split(fp_feed, legacy)
    assert [_span(fp_feed, k) for k in kids] == [(10.0, 40.0), (40.0, 70.0)]
    assert [o[1].stored for o in result['fingerprint_outcomes']] == [True, True]


@pytest.mark.parametrize('kind', ['trim', 'split'])
def test_legacy_with_only_out_of_scope_or_rejected_matches_reports_the_reason(
        fp_feed, fp_original, stub_fingerprinting, legacy, kind):
    _match(fp_feed, legacy, in_scope=False)
    _match(fp_feed, legacy, start=11.0)
    assert fp_feed.mark_fingerprint_match_rejected(legacy, SLUG, EPISODE, 11.0, 70.0)
    result = _trim(fp_feed, legacy)[1] if kind == 'trim' else _split(fp_feed, legacy)[1]
    assert [(o[1].stored, o[1].skipped_reason) for o in result['fingerprint_outcomes']] == [
        (False, 'original_not_retained')]
    assert fp_feed.get_audio_fingerprint(legacy)['fingerprint'] == OLD


def test_legacy_without_a_retained_original_reports_the_reason(fp_feed, stub_fingerprinting, legacy):
    _match(fp_feed, legacy)
    _, result = _trim(fp_feed, legacy)
    assert result['status'] == 'approved'
    assert [o[1].skipped_reason for o in result['fingerprint_outcomes']] == ['original_not_retained']


def test_legacy_route_response_carries_the_skip_reason(app_client, fp_feed, stub_fingerprinting, legacy):
    sid = _suggest(fp_feed, legacy, 'trim', {'text': SECOND_TEMPLATE})
    body = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve',
                           headers=csrf(app_client)).get_json()
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'original_not_retained')


def test_recorded_source_wins_over_a_newer_match_when_retained(fp_feed, fp_original, stub_fingerprinting, parent):
    _match(fp_feed, parent, start=20.0, end=50.0)
    _trim(fp_feed, parent)
    assert _span(fp_feed, parent) == (40.0, 70.0)


def test_recorded_source_gone_falls_back_to_a_retained_in_scope_match(fp_feed, stub_fingerprinting, parent):
    from api import get_storage
    other = 'b2c3d4e5f6a1'
    fp_feed.upsert_episode(SLUG, other, original_url='https://example.com/o.mp3', title='Other', status='processed')
    path = get_storage().get_original_path(SLUG, other)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ID3')
    fp_feed.upsert_episode(SLUG, other, original_file=path.name)
    fp_feed.save_original_transcript(
        SLUG, other, f'[00:00:10.000 --> 00:00:40.000] {FIRST_READ}\n[00:00:40.000 --> 00:01:10.000] {SECOND_TEMPLATE}')
    record_fingerprint_match(fp_feed, parent, other, SLUG, 10.0, 70.0, 0.9, 0.9, True, False)
    try:
        _, result = _trim(fp_feed, parent)
    finally:
        path.unlink(missing_ok=True)
    assert result['fingerprint_outcomes'][0][1].stored
    assert {str(call[0]) for call in stub_fingerprinting} == {str(path)}
    assert _span(fp_feed, parent) == (40.0, 70.0)


@pytest.mark.parametrize('setup', ['none', 'off', 'removed'])
def test_no_usable_audio_creates_none_and_reports_nothing(fp_feed, fp_original, stub_fingerprinting, legacy, setup):
    _match(fp_feed, legacy)
    if setup == 'none':
        fp_feed.delete_audio_fingerprint(legacy)
    elif setup == 'off':
        fp_feed.set_fingerprint_enabled(legacy, False)
    else:
        fp_feed.set_pattern_audio_removed(legacy, True)
    _, result = _trim(fp_feed, legacy)
    assert result['fingerprint_outcomes'] == []
    row = fp_feed.get_audio_fingerprint(legacy, with_blob=False)
    assert row is None or row['source_start'] is None


def test_undo_trim_of_a_legacy_fingerprint_rebuilds_from_the_resolved_span(
        fp_feed, fp_original, stub_fingerprinting, legacy):
    _match(fp_feed, legacy)
    sid, _ = _trim(fp_feed, legacy)
    assert _span(fp_feed, legacy) == (40.0, 70.0)
    result = pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert _span(fp_feed, legacy) == (10.0, 70.0)
    assert result['fingerprint_outcomes'][0][1].stored


def test_undo_of_an_unrebuilt_legacy_trim_leaves_the_fingerprint_alone(fp_feed, stub_fingerprinting, legacy):
    _match(fp_feed, legacy)
    sid, _ = _trim(fp_feed, legacy)
    result = pattern_cleanup.undo_suggestion(fp_feed, sid)
    assert result['fingerprint_outcomes'] == []
    assert fp_feed.get_audio_fingerprint(legacy)['fingerprint'] == OLD
