"""The audio detail reports whether the stored fingerprint can match under the current settings."""
import random

from tests.integration.fingerprint_helpers import csrf


def _stored(db, pid, ints):
    import acoustid
    data = acoustid.chromaprint.encode_fingerprint(ints, 1, True)
    db.create_audio_fingerprint(pid, data, 30.0)


def _detail(client, pid):
    return client.get(f'/api/v1/patterns/{pid}/audio', headers=csrf(client)).get_json()


def test_matchable_follows_the_minimum_coverage_setting(app_client, fp_feed, fp_pattern):
    rng = random.Random(3)
    # Under 20 s of evidence, so the coverage setting still decides
    _stored(fp_feed, fp_pattern, [rng.getrandbits(32) for _ in range(150)] + [7] * 50)
    assert _detail(app_client, fp_pattern)['matchable'] is True
    fp_feed.set_setting('fingerprint_min_coverage', '0.9')
    try:
        assert _detail(app_client, fp_pattern)['matchable'] is False
    finally:
        fp_feed.set_setting('fingerprint_min_coverage', '0.5')


def test_a_fingerprint_with_20_s_of_evidence_stays_matchable_at_full_coverage(app_client, fp_feed, fp_pattern):
    rng = random.Random(4)
    _stored(fp_feed, fp_pattern, [rng.getrandbits(32) for _ in range(300)] + [7] * 100)
    fp_feed.set_setting('fingerprint_min_coverage', '1.0')
    try:
        assert _detail(app_client, fp_pattern)['matchable'] is True
    finally:
        fp_feed.set_setting('fingerprint_min_coverage', '0.5')


def test_matchable_is_true_without_a_fingerprint(app_client, fp_pattern):
    assert _detail(app_client, fp_pattern)['matchable'] is True


def test_undecodable_fingerprint_is_not_matchable(app_client, fp_audio_pattern):
    assert _detail(app_client, fp_audio_pattern)['matchable'] is False
