"""Correction provenance + FP-text suppression (2.76.0).

pattern_corrections gains source_hold_reason (provenance for a
false_positive correction) and fp_suppressed (excludes a correction's
text_snippet from cross-episode FP matching once it's known to have come
from a rejected differential hold rather than a confirmed false positive).
"""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('fp_text_scoping_test_')

from main_app import app, db
from database.patterns import suppress_differential_fp_texts
from api.patterns import _handle_reject_correction


TRANSCRIPT_TEXT = (
    "[00:00:00.000 --> 00:01:00.000] This episode is brought to you by "
    "ExampleSponsor. Visit examplesponsor.com slash podcast for fifty percent "
    "off your first month. ExampleSponsor makes everything better and faster "
    "than the competition."
)


def _held_marker(start, end, hold_reason=None, detection_stage='claude'):
    marker = {
        'start': start, 'end': end, 'confidence': 0.9,
        'reason': 'sponsor read', 'was_cut': False,
        'held_for_review': True, 'detection_stage': detection_stage,
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.9},
    }
    if hold_reason is not None:
        marker['hold_reason'] = hold_reason
    return marker


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


def test_reject_differential_hold_writes_null_text_and_source_hold_reason():
    """Rejecting a held marker whose hold_reason is differential_uncorroborated
    must not mint cross-episode FP text: text_snippet is NULL, source_hold_reason
    is stamped, and the same-episode region is still recorded."""
    slug, episode_id = _make_episode('fp-scope-diff', 'ep-fp-scope-diff')
    db.save_episode_details(
        slug, episode_id, transcript_text=TRANSCRIPT_TEXT,
        ad_markers=[_held_marker(0.0, 60.0,
                                  hold_reason='differential_uncorroborated',
                                  detection_stage='dai_differential')],
        pending_review_count=1,
    )

    with app.test_request_context():
        _handle_reject_correction(db, slug, episode_id, {'start': 0.0, 'end': 60.0})

    row = db.get_connection().execute(
        """SELECT text_snippet, source_hold_reason FROM pattern_corrections
           WHERE episode_id = ? AND correction_type = 'false_positive'""",
        (episode_id,),
    ).fetchone()
    assert row['text_snippet'] is None
    assert row['source_hold_reason'] == 'differential_uncorroborated'

    assert db.get_podcast_false_positive_texts(slug) == []

    region = db.get_false_positive_corrections(episode_id)
    assert any(r['start'] == 0.0 and r['end'] == 60.0 for r in region)


def test_reject_dai_differential_stage_without_hold_reason_field_still_nulls_text():
    """detection_stage=='dai_differential' alone (hold_reason field absent,
    e.g. already popped or never set) must independently trigger the same
    null-text, stamped-source_hold_reason behavior as an explicit
    hold_reason=='differential_uncorroborated'."""
    slug, episode_id = _make_episode('fp-scope-diff-stage', 'ep-fp-scope-diff-stage')
    db.save_episode_details(
        slug, episode_id, transcript_text=TRANSCRIPT_TEXT,
        ad_markers=[_held_marker(0.0, 60.0, hold_reason=None,
                                  detection_stage='dai_differential')],
        pending_review_count=1,
    )

    with app.test_request_context():
        _handle_reject_correction(db, slug, episode_id, {'start': 0.0, 'end': 60.0})

    row = db.get_connection().execute(
        """SELECT text_snippet, source_hold_reason FROM pattern_corrections
           WHERE episode_id = ? AND correction_type = 'false_positive'""",
        (episode_id,),
    ).fetchone()
    assert row['text_snippet'] is None
    assert row['source_hold_reason'] == 'differential_uncorroborated'


def test_reject_claude_stage_hold_still_writes_text_snippet():
    """A claude-stage hold (e.g. reviewer_contradiction) is unaffected: reject
    still writes the extracted text_snippet, now also stamped with
    source_hold_reason, and the same-episode region is unchanged."""
    slug, episode_id = _make_episode('fp-scope-claude', 'ep-fp-scope-claude')
    db.save_episode_details(
        slug, episode_id, transcript_text=TRANSCRIPT_TEXT,
        ad_markers=[_held_marker(0.0, 60.0,
                                  hold_reason='reviewer_contradiction',
                                  detection_stage='claude')],
        pending_review_count=1,
    )

    with app.test_request_context():
        _handle_reject_correction(db, slug, episode_id, {'start': 0.0, 'end': 60.0})

    row = db.get_connection().execute(
        """SELECT text_snippet, source_hold_reason FROM pattern_corrections
           WHERE episode_id = ? AND correction_type = 'false_positive'""",
        (episode_id,),
    ).fetchone()
    assert row['text_snippet'] is not None
    assert len(row['text_snippet']) >= 50
    assert row['source_hold_reason'] == 'reviewer_contradiction'

    texts = db.get_podcast_false_positive_texts(slug)
    assert any(t['text'] == row['text_snippet'] for t in texts)

    region = db.get_false_positive_corrections(episode_id)
    assert any(r['start'] == 0.0 and r['end'] == 60.0 for r in region)
