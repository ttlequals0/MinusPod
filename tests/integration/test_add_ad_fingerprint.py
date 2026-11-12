"""Add ad stores a fingerprint of the marked span and reports the outcome."""
from tests.integration.fingerprint_helpers import EPISODE, SLUG, TEMPLATE, post_correction  # noqa: E402


def _create(app_client, start=10.0, end=40.0):
    return post_correction(app_client, {
        'type': 'create', 'start': start, 'end': end, 'sponsor': 'Acme',
        'text_template': TEMPLATE})


def test_add_ad_stores_a_fingerprint_of_the_marked_span(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    resp = _create(app_client)
    body = resp.get_json()
    assert resp.status_code == 200 and body['fingerprintStored'] is True
    assert 'fingerprintSkippedReason' not in body
    row = fp_feed.get_audio_fingerprint(body['pattern_id'])
    assert (row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end'], row['duration']) == (SLUG, EPISODE, 10.0, 40.0, 30.0)
    assert stub_fingerprinting == [(str(fp_original), 10.0, 30.0)]


def test_short_segment_reason_and_pattern_still_saved(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    body = _create(app_client, 10.0, 12.0).get_json()
    assert body['fingerprintStored'] is False
    assert body['fingerprintSkippedReason'] == 'segment_too_short'
    assert fp_feed.get_ad_pattern_by_id(body['pattern_id']) is not None
    assert fp_feed.get_audio_fingerprint(body['pattern_id']) is None


def test_original_not_retained_reason(app_client, fp_feed, stub_fingerprinting):
    resp = _create(app_client)
    body = resp.get_json()
    assert resp.status_code == 200
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'original_not_retained')
    assert fp_feed.get_ad_pattern_by_id(body['pattern_id']) is not None


def test_retained_flag_without_a_file_is_not_retained(app_client, fp_feed, stub_fingerprinting):
    fp_feed.upsert_episode(SLUG, EPISODE, original_file='gone.mp3')
    assert _create(app_client).get_json()['fingerprintSkippedReason'] == 'original_not_retained'


def test_failed_generation_reason(app_client, fp_feed, fp_original, monkeypatch):
    from audio_fingerprinter import AudioFingerprinter
    monkeypatch.setattr(AudioFingerprinter, '_find_fpcalc', lambda self: '/usr/bin/fpcalc')
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint', lambda *a, **k: None)
    body = _create(app_client).get_json()
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'fingerprint_failed')
    assert fp_feed.get_ad_pattern_by_id(body['pattern_id']) is not None


def test_storage_errors_never_fail_the_correction(app_client, fp_feed, fp_original, monkeypatch):
    from audio_fingerprinter import AudioFingerprinter

    def boom(self, *a, **k):
        raise RuntimeError('decoder crashed')
    monkeypatch.setattr(AudioFingerprinter, '_find_fpcalc', lambda self: '/usr/bin/fpcalc')
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint', boom)
    resp = _create(app_client)
    assert resp.status_code == 200
    assert resp.get_json()['fingerprintSkippedReason'] == 'fingerprint_failed'


def test_add_ad_stores_a_fingerprint_with_no_llm_provider(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    previous = fp_feed.get_setting('llm_provider')
    fp_feed.set_setting('llm_provider', 'none')
    try:
        body = _create(app_client).get_json()
    finally:
        fp_feed.set_setting('llm_provider', previous or 'anthropic')
    assert body['fingerprintStored'] is True
    assert fp_feed.get_audio_fingerprint(body['pattern_id']) is not None


def test_a_multi_sponsor_template_fingerprints_each_pattern(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    two = (TEMPLATE + ' Visit acme.com today. '
           'This segment is also sponsored by Beta Corp, the better way to bank your money. '
           'Go to betacorp.com now for a free trial and a bonus offer today.')
    body = post_correction(app_client, {
        'type': 'create', 'start': 10.0, 'end': 70.0, 'sponsor': 'Acme',
        'text_template': two}).get_json()
    ids = body['patternIds']
    assert body['fingerprintStored'] is True
    assert all(fp_feed.get_audio_fingerprint(i) is not None for i in ids)
