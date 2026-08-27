"""DB round-trip for addressing_log: record_addressing_log + get_addressing_stats
(random addressing mode A/B tracking)."""


def test_record_and_aggregate_roundtrip(temp_db):
    temp_db.record_addressing_log(
        'show-a', 'ep1', 'detection', 'random', 'timestamps', 10, 8)
    temp_db.record_addressing_log(
        'show-a', 'ep2', 'verification', 'random', 'timestamps', 5, 5)
    temp_db.record_addressing_log(
        'show-a', 'ep1', 'detection', 'segment_ids', 'segment_ids', 4, 4)

    stats = temp_db.get_addressing_stats()

    ts = stats['modes']['timestamps']
    assert ts['runs'] == 2
    assert ts['windowsJudged'] == 15
    assert ts['windowsCompliant'] == 13
    assert ts['compliancePct'] == round(100.0 * 13 / 15, 1)

    seg = stats['modes']['segment_ids']
    assert seg['runs'] == 1
    assert seg['windowsJudged'] == 4
    assert seg['windowsCompliant'] == 4
    assert seg['compliancePct'] == 100.0


def test_zero_row_modes_present_as_zeros(temp_db):
    stats = temp_db.get_addressing_stats()
    for mode in ('timestamps', 'segment_ids'):
        entry = stats['modes'][mode]
        assert entry == {
            'runs': 0, 'windowsJudged': 0, 'windowsCompliant': 0, 'compliancePct': 0.0,
        }


def test_only_one_mode_has_rows_other_stays_zero(temp_db):
    temp_db.record_addressing_log(
        'show-a', 'ep1', 'detection', 'timestamps', 'timestamps', 3, 3)
    stats = temp_db.get_addressing_stats()
    assert stats['modes']['timestamps']['runs'] == 1
    assert stats['modes']['segment_ids'] == {
        'runs': 0, 'windowsJudged': 0, 'windowsCompliant': 0, 'compliancePct': 0.0,
    }


def test_podcast_slug_filter(temp_db):
    temp_db.record_addressing_log(
        'show-a', 'ep1', 'detection', 'timestamps', 'timestamps', 10, 10)
    temp_db.record_addressing_log(
        'show-b', 'ep1', 'detection', 'timestamps', 'timestamps', 4, 0)

    stats_a = temp_db.get_addressing_stats(podcast_slug='show-a')
    assert stats_a['modes']['timestamps']['runs'] == 1
    assert stats_a['modes']['timestamps']['windowsJudged'] == 10
    assert stats_a['modes']['timestamps']['compliancePct'] == 100.0

    stats_b = temp_db.get_addressing_stats(podcast_slug='show-b')
    assert stats_b['modes']['timestamps']['runs'] == 1
    assert stats_b['modes']['timestamps']['compliancePct'] == 0.0

    stats_all = temp_db.get_addressing_stats()
    assert stats_all['modes']['timestamps']['runs'] == 2


def test_yield_columns_exist_and_are_nullable(temp_db):
    conn = temp_db.get_connection()
    cols = {row['name']: row for row in
            conn.execute("PRAGMA table_info(addressing_log)").fetchall()}
    for col in ('ads_proposed', 'ads_kept', 'ads_dropped_invalid_ref',
                'ads_dropped_out_of_window', 'ads_dropped_too_long'):
        assert col in cols, f"missing column {col}"
        # NULL must be storable: it is the marker for pre-yield history.
        assert cols[col]['notnull'] == 0, f"{col} must be nullable"
        assert cols[col]['dflt_value'] is None, f"{col} must have no default"
