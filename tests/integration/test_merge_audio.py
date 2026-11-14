"""Merging patterns keeps the keeper's audio and drops the folded patterns' audio and history."""
from tests.integration.fingerprint_helpers import SLUG, TEMPLATE, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match


def _make(db, suffix):
    return db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=f'{TEMPLATE} {suffix}')


def _audio(db, pid, matches):
    db.create_audio_fingerprint(pid, b'AQAA', 6.0)
    for i in range(matches):
        record_fingerprint_match(db, pid, f'a1b2c3d4e5f{i}', SLUG, 10.0, 40.0, 0.9, 0.8, True, False)


def _merge(client, keep, folded):
    return client.post('/api/v1/patterns/merge', json={'keep_id': keep, 'merge_ids': folded},
                       headers=csrf(client))


def test_merge_drops_folded_audio_and_history_and_keeps_the_keepers(app_client, fp_feed):
    keep, folded = _make(fp_feed, 'one'), _make(fp_feed, 'two')
    _audio(fp_feed, keep, 1)
    _audio(fp_feed, folded, 3)
    assert _merge(app_client, keep, [folded]).status_code == 200
    assert fp_feed.count_fingerprint_matches(folded) == 0
    assert fp_feed.get_audio_fingerprint(folded) is None
    assert fp_feed.count_fingerprint_matches(keep) == 1
    assert fp_feed.get_audio_fingerprint(keep) is not None


def test_merge_does_not_move_folded_audio_to_a_keeper_without_any(app_client, fp_feed):
    keep, folded = _make(fp_feed, 'one'), _make(fp_feed, 'two')
    _audio(fp_feed, folded, 2)
    assert _merge(app_client, keep, [folded]).status_code == 200
    assert fp_feed.get_audio_fingerprint(keep) is None
    assert fp_feed.count_fingerprint_matches(keep) == 0
    assert fp_feed.get_ad_pattern_by_id(keep)['audio_removed'] == 0


def test_merge_leaves_the_keepers_audio_removed_marker(app_client, fp_feed):
    keep, folded = _make(fp_feed, 'one'), _make(fp_feed, 'two')
    fp_feed.set_pattern_audio_removed(keep, True)
    _audio(fp_feed, folded, 1)
    assert _merge(app_client, keep, [folded]).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(keep)['audio_removed'] == 1
    assert fp_feed.get_audio_fingerprint(keep) is None
