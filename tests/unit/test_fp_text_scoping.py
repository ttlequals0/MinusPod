"""Correction provenance + FP-text suppression (2.76.0).

pattern_corrections gains source_hold_reason (provenance for a
false_positive correction) and fp_suppressed (excludes a correction's
text_snippet from cross-episode FP matching once it's known to have come
from a rejected differential hold rather than a confirmed false positive).
"""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('fp_text_scoping_test_')

from main_app import db
from database.patterns import suppress_differential_fp_texts


def _make_episode(slug='fp-scope-test', episode_id='ep-fp-scope-001'):
    db.create_podcast(slug, 'https://example.com/feed.xml', 'FP Scope Test')
    db.upsert_episode(
        slug=slug,
        episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode',
        original_duration=600.0,
    )
    return slug, episode_id


def test_source_hold_reason_roundtrips():
    slug, episode_id = _make_episode()
    correction_id = db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 10.0, 'end': 20.0},
        text_snippet='x' * 60,
        source_hold_reason='differential_uncorroborated',
    )

    row = db.get_connection().execute(
        "SELECT source_hold_reason FROM pattern_corrections WHERE id = ?",
        (correction_id,),
    ).fetchone()
    assert row['source_hold_reason'] == 'differential_uncorroborated'


def test_fp_suppressed_excluded_from_false_positive_texts():
    slug, episode_id = _make_episode('fp-scope-test-2', 'ep-fp-scope-002')

    suppressed_id = db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 10.0, 'end': 20.0},
        text_snippet='suppressed text ' * 5,
    )
    db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 200.0, 'end': 220.0},
        text_snippet='unsuppressed text ' * 5,
    )
    db.get_connection().execute(
        "UPDATE pattern_corrections SET fp_suppressed = 1 WHERE id = ?",
        (suppressed_id,),
    )
    db.get_connection().commit()

    texts = db.get_podcast_false_positive_texts('fp-scope-test-2')
    snippets = [t['text'] for t in texts]
    assert 'unsuppressed text ' * 5 in snippets
    assert 'suppressed text ' * 5 not in snippets


def test_backfill_suppresses_only_matching_differential_marker():
    slug, episode_id = _make_episode('fp-scope-test-3', 'ep-fp-scope-003')

    markers = [
        {
            'start': 100.0, 'end': 150.0, 'sponsor': None, 'reason': '',
            'confidence': 0.5, 'detection_stage': 'dai_differential',
            'pattern_id': None,
        },
    ]
    db.save_episode_details(slug, episode_id, ad_markers=markers)

    matching_id = db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 100.2, 'end': 149.8},
        text_snippet='x' * 60,
    )
    non_matching_id = db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 300.0, 'end': 350.0},
        text_snippet='y' * 60,
    )

    before_count = db.get_connection().execute(
        "SELECT COUNT(*) AS n FROM pattern_corrections"
    ).fetchone()['n']

    suppressed_count = suppress_differential_fp_texts(db)
    assert suppressed_count == 1

    after_count = db.get_connection().execute(
        "SELECT COUNT(*) AS n FROM pattern_corrections"
    ).fetchone()['n']
    assert after_count == before_count

    rows = {
        r['id']: r['fp_suppressed']
        for r in db.get_connection().execute(
            "SELECT id, fp_suppressed FROM pattern_corrections WHERE id IN (?, ?)",
            (matching_id, non_matching_id),
        ).fetchall()
    }
    assert rows[matching_id] == 1
    assert rows[non_matching_id] in (0, None)


def test_backfill_skips_ambiguous_cross_podcast_episode_id():
    shared_episode_id = 'ep-fp-scope-shared-001'
    slug_a, _ = _make_episode('fp-scope-test-a', shared_episode_id)
    slug_b, _ = _make_episode('fp-scope-test-b', shared_episode_id)

    markers = [
        {
            'start': 100.0, 'end': 150.0, 'sponsor': None, 'reason': '',
            'confidence': 0.5, 'detection_stage': 'dai_differential',
            'pattern_id': None,
        },
    ]
    db.save_episode_details(slug_b, shared_episode_id, ad_markers=markers)

    correction_id = db.create_pattern_correction(
        correction_type='false_positive',
        episode_id=shared_episode_id,
        podcast_title='FP Scope Test',
        original_bounds={'start': 100.2, 'end': 149.8},
        text_snippet='z' * 60,
    )

    before_count = db.get_connection().execute(
        "SELECT COUNT(*) AS n FROM pattern_corrections"
    ).fetchone()['n']

    suppressed_count = suppress_differential_fp_texts(db)
    assert suppressed_count == 0

    after_count = db.get_connection().execute(
        "SELECT COUNT(*) AS n FROM pattern_corrections"
    ).fetchone()['n']
    assert after_count == before_count

    row = db.get_connection().execute(
        "SELECT fp_suppressed FROM pattern_corrections WHERE id = ?",
        (correction_id,),
    ).fetchone()
    assert row['fp_suppressed'] in (0, None)
