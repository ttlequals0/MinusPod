"""Reviewer verdict aggregation tests."""


def _insert_verdict(db, verdict, *, pass_num=1, success=1):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO ad_reviewer_log
           (episode_id, podcast_id, pass, pool, original_start, original_end,
            verdict, model_used, success)
           VALUES ('episode', NULL, ?, 'accepted', 10.0, 20.0, ?, 'model', ?)""",
        (pass_num, verdict, success),
    )
    conn.commit()


def test_reviewer_stats_empty_includes_inconclusive_zero(temp_db):
    stats = temp_db.get_reviewer_stats()

    assert stats['verdictCounts'] == {
        'confirmed': 0,
        'adjust': 0,
        'reject': 0,
        'resurrect': 0,
        'inconclusive': 0,
        'failure': 0,
    }


def test_reviewer_stats_counts_inconclusive_separately(temp_db):
    for verdict in ('confirmed', 'adjust', 'reject', 'resurrect', 'failure'):
        _insert_verdict(temp_db, verdict)
    _insert_verdict(temp_db, 'inconclusive', success=1)
    _insert_verdict(temp_db, 'inconclusive', pass_num=2, success=1)

    stats = temp_db.get_reviewer_stats()

    assert stats['verdictCounts'] == {
        'confirmed': 1,
        'adjust': 1,
        'reject': 1,
        'resurrect': 1,
        'inconclusive': 2,
        'failure': 1,
    }
    assert stats['totalReviews'] == 7
    assert stats['failureCount'] == 1
    assert stats['resurrectionCount'] == 1
