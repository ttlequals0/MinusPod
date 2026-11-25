import pytest

from description_chapters import description_trim_spans, parse_description_timestamps

DESC = (
    "<p>(00:00) - Weekly Show</p>"
    "<p>(31:26) - Throwback 10-8-18 - Host rambles</p>"
    "<p>(50:00) - Closing Thoughts</p>"
)


@pytest.mark.parametrize('line,expected', [
    ('(00:05) - Intro', [(5.0, 'Intro')]),
    ('(1:02:03) Deep Dive', [(3723.0, 'Deep Dive')]),
    ('12:30 - Plain Line', [(750.0, 'Plain Line')]),
    ('[05:00] Bracketed', [(300.0, 'Bracketed')]),
    ('02:30 \u2013 En Dash', [(150.0, 'En Dash')]),
    ('(02:30) \u2014 Em Dash', [(150.0, 'Em Dash')]),
])
def test_parser_forms(line, expected):
    assert parse_description_timestamps(line) == expected


def test_parser_html_entities_and_sorting():
    html = "<p>(10:00) - Q&amp;A</p><p>(02:00) - Intro &#8211; Part 1</p>"
    assert parse_description_timestamps(html) == [(120.0, 'Intro – Part 1'), (600.0, 'Q&A')]


def test_parser_ignores_mid_line_and_empty():
    assert parse_description_timestamps('We covered 10:00 in detail') == []
    assert parse_description_timestamps(None) == []


def test_to_end_span():
    assert description_trim_spans(DESC, '*Throwback*', 'to_end', 3600.0) == [(1886.0, 3600.0, 'Throwback 10-8-18 - Host rambles')]


def test_to_next_span():
    assert description_trim_spans(DESC, '*Throwback*', 'to_next', 3600.0) == [(1886.0, 3000.0, 'Throwback 10-8-18 - Host rambles')]


def test_to_next_last_chapter_runs_to_end():
    assert description_trim_spans(DESC, 'Closing*', 'to_next', 3600.0) == [(3000.0, 3600.0, 'Closing Thoughts')]


def test_end_within_one_second_is_to_end():
    assert description_trim_spans(DESC, 'Closing*', 'to_next', 3000.5) == [(3000.0, 3000.5, 'Closing Thoughts')]
    assert description_trim_spans(DESC, '*Throwback*', 'to_next', 3000.5) == [(1886.0, 3000.5, 'Throwback 10-8-18 - Host rambles')]


def test_match_is_case_insensitive_whole_title():
    assert description_trim_spans(DESC, 'throwback', 'to_end', 3600.0) == []
    assert description_trim_spans(DESC, '*THROWBACK*', 'to_end', 3600.0) != []


def test_no_match_or_no_pattern_returns_empty():
    assert description_trim_spans(DESC, '*Nothing*', 'to_end', 3600.0) == []
    assert description_trim_spans(DESC, None, 'to_end', 3600.0) == []
    assert description_trim_spans('', '*x*', 'to_end', 3600.0) == []


def test_start_past_episode_end_returns_empty():
    assert description_trim_spans(DESC, '*Throwback*', 'to_end', 1000.0) == []


CHAPTERS = [
    {'startTime': 1, 'title': 'Opening'},
    {'startTime': 1200, 'title': 'Listener Donations'},
    {'startTime': 1500, 'title': 'Main Topic'},
]


def test_chapter_span_to_end_and_to_next():
    from description_chapters import chapter_trim_spans
    assert chapter_trim_spans(CHAPTERS, '*Donations*', 'to_end', 3000.0) == [
        (1200.0, 3000.0, 'Listener Donations')]
    assert chapter_trim_spans(CHAPTERS, '*Donations*', 'to_next', 3000.0) == [
        (1200.0, 1500.0, 'Listener Donations')]


def test_chapter_span_last_chapter_runs_to_end_and_no_match_is_empty():
    from description_chapters import chapter_trim_spans
    assert chapter_trim_spans(CHAPTERS, 'Main*', 'to_next', 3000.0) == [(1500.0, 3000.0, 'Main Topic')]
    assert chapter_trim_spans(CHAPTERS, '*Nothing*', 'to_end', 3000.0) == []
    assert chapter_trim_spans([], '*x*', 'to_end', 3000.0) == []
    assert chapter_trim_spans(CHAPTERS, None, 'to_end', 3000.0) == []


def test_to_next_cuts_every_matching_chapter_and_to_end_only_the_first():
    chapters = [
        {'startTime': 1, 'title': 'Intro'}, {'startTime': 100, 'title': 'Donations'},
        {'startTime': 200, 'title': 'Talk'}, {'startTime': 300, 'title': 'Donations'},
        {'startTime': 400, 'title': 'Talk 2'}, {'startTime': 500, 'title': 'Donations'},
    ]
    from description_chapters import chapter_trim_spans
    assert chapter_trim_spans(chapters, 'Donations', 'to_next', 600.0) == [
        (100.0, 200.0, 'Donations'), (300.0, 400.0, 'Donations'), (500.0, 600.0, 'Donations')]
    assert chapter_trim_spans(chapters, 'Donations', 'to_end', 600.0) == [(100.0, 600.0, 'Donations')]


def test_to_next_merges_adjacent_matches_and_skip_starts_still_end_the_previous():
    from description_chapters import chapter_trim_spans
    chapters = [{'startTime': 100, 'title': 'Donations'}, {'startTime': 200, 'title': 'More Donations'},
                {'startTime': 300, 'title': 'Talk'}, {'startTime': 400, 'title': 'Donations'},
                {'startTime': 500, 'title': 'Donations'}]
    assert chapter_trim_spans(chapters, '*Donations', 'to_next', 700.0) == [
        (100.0, 300.0, 'Donations'), (400.0, 700.0, 'Donations')]
    assert chapter_trim_spans(chapters, '*Donations', 'to_next', 700.0, skip_starts=[400]) == [
        (100.0, 300.0, 'Donations'), (500.0, 700.0, 'Donations')]


def test_end_snap_does_not_merge_a_short_kept_chapter_near_the_end():
    from description_chapters import chapter_trim_spans
    chapters = [{'startTime': 100, 'title': 'Donations'}, {'startTime': 999.5, 'title': 'Talk'},
                {'startTime': 999.8, 'title': 'Donations'}]
    assert chapter_trim_spans(chapters, 'Donations', 'to_next', 1000.0) == [
        (100.0, 999.5, 'Donations'), (999.8, 1000.0, 'Donations')]


def test_description_to_next_cuts_every_matching_timestamp_line():
    html = ("<p>(00:00) - Show</p><p>(10:00) - Donations</p><p>(20:00) - Talk</p>"
            "<p>(30:00) - Donations</p><p>(40:00) - Outro</p>")
    assert description_trim_spans(html, 'Donations', 'to_next', 3000.0) == [
        (600.0, 1200.0, 'Donations'), (1800.0, 2400.0, 'Donations')]


from description_chapters import remap_description_timestamps  # noqa: E402

CUT = [{'start': 60.0, 'end': 120.0, 'replacement_duration': 0.0}]


def test_remap_shifts_token_after_cut():
    assert remap_description_timestamps('05:00 Topic', CUT, replacement_duration=0.0) == '04:00 Topic'


def test_remap_token_inside_cut_collapses_to_cut_start():
    assert remap_description_timestamps('01:30 Ad', CUT, replacement_duration=0.0) == '01:00 Ad'


def test_remap_replacement_audio_offsets_shift():
    cuts = [{'start': 60.0, 'end': 120.0, 'replacement_duration': 2.0}]
    assert remap_description_timestamps('05:00', cuts, replacement_duration=0.0) == '04:02'


@pytest.mark.parametrize('text', ['Live at 10:00 AM', 'at 10:00 pm today', '9:30 a.m. start'])
def test_remap_leaves_clock_times(text):
    assert remap_description_timestamps(text, CUT, replacement_duration=0.0) == text


@pytest.mark.parametrize('text,expected', [
    ('5:00 x', '4:00 x'),
    ('00:65', '00:65'),
    ('1:05:00 x', '1:04:00 x'),
    ('01:05:00 x', '01:04:00 x'),
    ('00:05 start', '00:05 start'),
])
def test_remap_preserves_shape(text, expected):
    assert remap_description_timestamps(text, CUT, replacement_duration=0.0) == expected


def test_remap_hours_dropped_to_minutes_keeps_hours_shape():
    cuts = [{'start': 0.0, 'end': 3600.0, 'replacement_duration': 0.0}]
    assert remap_description_timestamps('1:05:00', cuts, replacement_duration=0.0) == '0:05:00'


def test_remap_no_cuts_returns_identical():
    text = '<p>05:00 Topic</p>'
    assert remap_description_timestamps(text, [], replacement_duration=0.0) is text
    assert remap_description_timestamps(text, None, replacement_duration=0.0) is text


def test_remap_preserves_html():
    out = remap_description_timestamps('<p><b>05:00</b> - A &amp; B<br>(06:00) C</p>', CUT,
                                       replacement_duration=0.0)
    assert out == '<p><b>04:00</b> - A &amp; B<br>(05:00) C</p>'


@pytest.mark.parametrize('original', [30.0, 90.0, 300.0])
def test_shift_for_merged_cuts_matches_adjust_timestamp(original):
    from utils.time import adjust_timestamp, merge_cut_spans, shift_for_merged_cuts
    cuts = [{'start': 60.0, 'end': 120.0, 'replacement_duration': 2.0},
            {'start': 100.0, 'end': 150.0}]
    merged = merge_cut_spans(cuts, default_replacement=1.0)
    assert shift_for_merged_cuts(original, merged) == adjust_timestamp(original, cuts, 1.0)


@pytest.mark.parametrize('text', [
    'See John 3:16 for more',
    'Meet at 14:30 UTC today',
    '<a href="https://example.com/v?t=1:30">link</a>',
])
def test_remap_leaves_tokens_outside_chapter_line_position(text):
    assert remap_description_timestamps(text, CUT, replacement_duration=0.0) == text


@pytest.mark.parametrize('text,expected', [
    ('<p>Intro</p><br>12:34 Topic', '<p>Intro</p><br>11:34 Topic'),
    ('<p>12:34 Topic</p>', '<p>11:34 Topic</p>'),
    ('[12:34] Topic', '[11:34] Topic'),
    ('Intro\n(12:34) Topic', 'Intro\n(11:34) Topic'),
])
def test_remap_shifts_line_start_tokens(text, expected):
    assert remap_description_timestamps(text, CUT, replacement_duration=0.0) == expected


def test_remap_keeps_mmss_shape_past_an_hour():
    cuts = [{'start': 0.0, 'end': 60.0, 'replacement_duration': 0.0}]
    assert remap_description_timestamps('70:00 Late', cuts, replacement_duration=0.0) == '69:00 Late'


def test_has_chapter_timestamp():
    from description_chapters import has_chapter_timestamp
    assert has_chapter_timestamp('<p>05:00 Topic</p>')
    assert not has_chapter_timestamp('John 3:16')
    assert not has_chapter_timestamp(None)
