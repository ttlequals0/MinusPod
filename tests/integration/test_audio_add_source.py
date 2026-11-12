"""The audio detail names the span a missing fingerprint could be built from."""
from tests.integration.fingerprint_helpers import EPISODE, SLUG, TEMPLATE, csrf


def _detail(client, pid):
    return client.get(f'/api/v1/patterns/{pid}/audio', headers=csrf(client)).get_json()


def _pattern(db, **kw):
    args = dict(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE, created_from_episode_id=EPISODE)
    args.update(kw)
    return db.create_ad_pattern(**args)


def _markers(db, markers):
    db.save_episode_details(SLUG, EPISODE, ad_markers=markers)


def test_names_the_marker_of_the_pattern_in_its_origin_episode(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid},
                       {'start': 10.0, 'end': 40.0, 'pattern_id': pid + 1}])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 50.0, 'end': 80.0, 'available': True}


def test_earliest_marker_wins(app_client, fp_feed):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 90.0, 'end': 100.0, 'pattern_id': pid},
                       {'start': 20.0, 'end': 50.0, 'pattern_id': pid}])
    body = _detail(app_client, pid)['addSource']
    assert (body['start'], body['end'], body['available']) == (20.0, 50.0, False)


def test_adjusted_marker_uses_the_reviewer_original_span(app_client, fp_feed):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': pid, 'reviewer_verdict': 'adjust',
                        'reviewer_original_start': 5.0, 'reviewer_original_end': 35.0}])
    body = _detail(app_client, pid)['addSource']
    assert (body['start'], body['end']) == (5.0, 35.0)


def test_null_when_the_pattern_has_audio(app_client, fp_feed, fp_audio_pattern):
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': fp_audio_pattern}])
    assert _detail(app_client, fp_audio_pattern)['addSource'] is None


def test_null_without_origin_episode_or_marker(app_client, fp_feed):
    no_origin = _pattern(fp_feed, created_from_episode_id=None)
    no_marker = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': no_origin}])
    assert _detail(app_client, no_origin)['addSource'] is None
    assert _detail(app_client, no_marker)['addSource'] is None


def test_null_for_a_pattern_without_a_podcast(app_client, fp_feed):
    pid = _pattern(fp_feed, scope='global', podcast_id=None)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] is None
