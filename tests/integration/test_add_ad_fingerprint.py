"""Add ad stores a fingerprint of the marked span and reports the outcome."""
import pytest

from tests.integration.fingerprint_helpers import (  # noqa: E402
    EPISODE, FIRST_READ, SECOND_TEMPLATE, SLUG, TEMPLATE, TWO_SPONSORS, post_correction, seed_timed_transcript,
    seed_two_sponsor_transcript,
)
from api.patterns import _own_pattern_spans  # noqa: E402  must follow the helpers, which bootstrap the data dir


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


def _create_two_sponsors(app_client):
    return post_correction(app_client, {
        'type': 'create', 'start': 10.0, 'end': 70.0, 'sponsor': 'Acme',
        'text_template': TWO_SPONSORS}).get_json()


def test_a_multi_sponsor_template_fingerprints_each_pattern_over_its_own_span(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    body = _create_two_sponsors(app_client)
    first, second = body['patternIds']
    assert body['fingerprintStored'] is True and 'fingerprintSkippedReason' not in body
    spans = {pid: (r['source_start'], r['source_end'], r['duration'])
             for pid in (first, second) for r in [fp_feed.get_audio_fingerprint(pid)]}
    assert spans == {first: (10.0, 40.0, 30.0), second: (40.0, 70.0, 30.0)}
    assert stub_fingerprinting == [(str(fp_original), 10.0, 30.0), (str(fp_original), 40.0, 30.0)]


def test_a_typed_template_that_is_not_in_the_transcript_stores_the_marked_span(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    fp_feed.save_original_transcript(SLUG, EPISODE, '[00:00:10.000 --> 00:01:10.000] Something else entirely.')
    body = _create(app_client).get_json()
    row = fp_feed.get_audio_fingerprint(body['pattern_id'])
    assert body['fingerprintStored'] is True
    assert (row['source_start'], row['source_end']) == (10.0, 40.0)


def test_a_split_text_that_yields_one_pattern_stores_the_marked_span(
        app_client, fp_feed, fp_original, stub_fingerprinting, monkeypatch):
    monkeypatch.setattr('api.patterns.MAX_PATTERN_CHARS', len(FIRST_READ) + 5)
    body = _create_two_sponsors(app_client)
    assert len(body['patternIds']) == 1
    row = fp_feed.get_audio_fingerprint(body['pattern_id'])
    assert body['fingerprintStored'] is True
    assert (row['source_start'], row['source_end']) == (10.0, 70.0)


def test_a_split_span_without_a_derivable_range_stores_nothing(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    body = _create_two_sponsors(app_client)
    assert len(body['patternIds']) == 2
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (
        False, 'span_covers_multiple_patterns')
    assert all(fp_feed.get_audio_fingerprint(pid) is None for pid in body['patternIds'])
    assert stub_fingerprinting == []


def test_a_pattern_whose_text_is_not_in_the_transcript_stores_nothing(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    fp_feed.save_original_transcript(SLUG, EPISODE, '[00:00:10.000 --> 00:01:10.000] Something else entirely.')
    body = _create_two_sponsors(app_client)
    assert body['fingerprintSkippedReason'] == 'span_covers_multiple_patterns'


def test_add_ad_never_replaces_the_audio_of_a_reused_pattern(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    first_run = _create_two_sponsors(app_client)
    before = {pid: fp_feed.get_audio_fingerprint(pid)['fingerprint'] for pid in first_run['patternIds']}
    stub_fingerprinting.clear()
    second_run = _create_two_sponsors(app_client)
    assert second_run['patternIds'] == first_run['patternIds']
    assert (second_run['fingerprintStored'], second_run['fingerprintSkippedReason']) == (
        False, 'fingerprint_exists')
    assert {pid: fp_feed.get_audio_fingerprint(pid)['fingerprint'] for pid in before} == before
    assert stub_fingerprinting == []


def test_add_ad_leaves_a_disabled_fingerprint_alone(app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    first_run = _create_two_sponsors(app_client)
    first = first_run['patternIds'][0]
    fp_feed.set_fingerprint_enabled(first, False)
    _create_two_sponsors(app_client)
    assert fp_feed.get_audio_fingerprint(first)['enabled'] == 0


def _spans(db, body):
    return [(r['source_start'], r['source_end'])
            for pid in body['patternIds'] for r in [db.get_audio_fingerprint(pid)]]


def test_a_boundary_segment_is_split_between_two_patterns_by_text_offset(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    half = len(SECOND_TEMPLATE) // 2
    cut = SECOND_TEMPLATE.rfind(' ', 0, half)
    long_segment = FIRST_READ + ' ' + SECOND_TEMPLATE[:cut]
    seed_timed_transcript(fp_feed, [(10.0, 40.0, long_segment), (40.0, 70.0, SECOND_TEMPLATE[cut + 1:])])
    (a_start, a_end), (b_start, b_end) = _spans(fp_feed, _create_two_sponsors(app_client))
    assert a_start == 10.0 and b_end == 70.0
    assert a_end == pytest.approx(10.0 + 30.0 * len(FIRST_READ) / len(long_segment))
    assert b_start == pytest.approx(10.0 + 30.0 * (len(FIRST_READ) + 1) / len(long_segment))
    assert a_end < b_start < 40.0


def test_a_boundary_segment_without_timing_goes_whole_to_the_first_pattern(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    cut = FIRST_READ.rfind(' ', 0, len(FIRST_READ) - 10)
    cut2 = SECOND_TEMPLATE.rfind(' ', 0, 40)
    seed_timed_transcript(fp_feed, [
        (10.0, 25.0, FIRST_READ[:cut]),
        (25.0, 25.0, FIRST_READ[cut + 1:] + ' ' + SECOND_TEMPLATE[:cut2]),
        (40.0, 70.0, SECOND_TEMPLATE[cut2 + 1:])])
    (a_start, a_end), (b_start, b_end) = _spans(fp_feed, _create_two_sponsors(app_client))
    assert (a_start, a_end) == (10.0, 25.0)
    assert (b_start, b_end) == (40.0, 70.0)


THIRD_READ = ('This episode is brought to you by Gamma Inc, the smart way to insure your home. '
              'Visit gammainc.com for a quote today and save.')
THREE_SPONSORS = TWO_SPONSORS + ' ' + THIRD_READ


class _FakeDb:
    def __init__(self, transcript, templates):
        self._transcript, self._templates = transcript, templates

    def get_transcript_for_timestamps(self, slug, episode_id):
        return self._transcript

    def get_ad_patterns_by_ids(self, pattern_ids):
        return {pid: {'text_template': self._templates[pid]} for pid in pattern_ids}


def _own(timed_lines, templates, text=THREE_SPONSORS, start=10.0, end=100.0):
    def stamp(sec):
        return f'{int(sec // 60):02d}:{sec % 60:06.3f}'
    transcript = '\n'.join(f'[00:{stamp(a)} --> 00:{stamp(b)}] {line}' for a, b, line in timed_lines)
    return _own_pattern_spans(_FakeDb(transcript, templates), SLUG, EPISODE, list(templates), start, end, text)


def test_three_patterns_across_segments_each_get_their_own_span():
    own = _own([(10.0, 40.0, FIRST_READ), (40.0, 70.0, SECOND_TEMPLATE), (70.0, 100.0, THIRD_READ)],
               {1: FIRST_READ, 2: SECOND_TEMPLATE, 3: THIRD_READ})
    assert own == {1: (10.0, 40.0), 2: (40.0, 70.0), 3: (70.0, 100.0)}


def test_three_patterns_inside_one_segment_get_ordered_disjoint_spans():
    own = _own([(10.0, 100.0, THREE_SPONSORS)], {1: FIRST_READ, 2: SECOND_TEMPLATE, 3: THIRD_READ})
    (a0, a1), (b0, b1), (c0, c1) = own[1], own[2], own[3]
    assert a0 == 10.0 and c1 == 100.0
    assert a0 < a1 <= b0 < b1 <= c0 < c1


def test_overlapping_templates_are_clamped_earlier_pattern_first():
    own = _own([(10.0, 40.0, FIRST_READ), (40.0, 70.0, SECOND_TEMPLATE), (70.0, 100.0, THIRD_READ)],
               {1: FIRST_READ, 2: FIRST_READ + ' ' + SECOND_TEMPLATE, 3: THIRD_READ})
    assert own == {1: (10.0, 40.0), 2: (40.0, 70.0), 3: (70.0, 100.0)}


def test_a_template_wholly_inside_an_earlier_one_gets_no_span():
    own = _own([(10.0, 40.0, FIRST_READ), (40.0, 70.0, SECOND_TEMPLATE), (70.0, 100.0, THIRD_READ)],
               {1: FIRST_READ + ' ' + SECOND_TEMPLATE, 2: SECOND_TEMPLATE, 3: THIRD_READ})
    assert own == {1: (10.0, 70.0), 2: None, 3: (70.0, 100.0)}


def test_a_multi_pattern_request_resolves_and_probes_the_original_once(
        app_client, fp_feed, fp_original, stub_fingerprinting, monkeypatch):
    import api.patterns as patterns_api
    from audio_fingerprinter import AudioFingerprinter
    resolved, probed = [], []
    real_resolve = patterns_api.resolve_retained_original
    monkeypatch.setattr(patterns_api, 'resolve_retained_original',
                        lambda *a: resolved.append(a) or real_resolve(*a))
    monkeypatch.setattr(AudioFingerprinter, '_get_audio_duration',
                        lambda self, path: probed.append(path) or 300.0)
    seed_two_sponsor_transcript(fp_feed)
    body = _create_two_sponsors(app_client)
    assert len(body['patternIds']) == 2 and body['fingerprintStored'] is True
    assert len(resolved) == 1 and len(probed) == 1
