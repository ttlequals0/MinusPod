import pytest

from description_chapters import description_trim_span, parse_description_timestamps

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
    assert description_trim_span(DESC, '*Throwback*', 'to_end', 3600.0) == (1886.0, 3600.0, 'Throwback 10-8-18 - Host rambles')


def test_to_next_span():
    assert description_trim_span(DESC, '*Throwback*', 'to_next', 3600.0) == (1886.0, 3000.0, 'Throwback 10-8-18 - Host rambles')


def test_to_next_last_chapter_runs_to_end():
    assert description_trim_span(DESC, 'Closing*', 'to_next', 3600.0) == (3000.0, 3600.0, 'Closing Thoughts')


def test_end_within_one_second_is_to_end():
    assert description_trim_span(DESC, 'Closing*', 'to_next', 3000.5) == (3000.0, 3000.5, 'Closing Thoughts')
    assert description_trim_span(DESC, '*Throwback*', 'to_next', 3000.5) == (1886.0, 3000.5, 'Throwback 10-8-18 - Host rambles')


def test_match_is_case_insensitive_whole_title():
    assert description_trim_span(DESC, 'throwback', 'to_end', 3600.0) is None
    assert description_trim_span(DESC, '*THROWBACK*', 'to_end', 3600.0) is not None


def test_no_match_or_no_pattern_returns_none():
    assert description_trim_span(DESC, '*Nothing*', 'to_end', 3600.0) is None
    assert description_trim_span(DESC, None, 'to_end', 3600.0) is None
    assert description_trim_span('', '*x*', 'to_end', 3600.0) is None


def test_start_past_episode_end_returns_none():
    assert description_trim_span(DESC, '*Throwback*', 'to_end', 1000.0) is None


CHAPTERS = [
    {'startTime': 1, 'title': 'Opening'},
    {'startTime': 1200, 'title': 'Listener Donations'},
    {'startTime': 1500, 'title': 'Main Topic'},
]


def test_chapter_span_to_end_and_to_next():
    from description_chapters import chapter_trim_span
    assert chapter_trim_span(CHAPTERS, '*Donations*', 'to_end', 3000.0) == (
        1200.0, 3000.0, 'Listener Donations')
    assert chapter_trim_span(CHAPTERS, '*Donations*', 'to_next', 3000.0) == (
        1200.0, 1500.0, 'Listener Donations')


def test_chapter_span_last_chapter_runs_to_end_and_no_match_is_none():
    from description_chapters import chapter_trim_span
    assert chapter_trim_span(CHAPTERS, 'Main*', 'to_next', 3000.0) == (1500.0, 3000.0, 'Main Topic')
    assert chapter_trim_span(CHAPTERS, '*Nothing*', 'to_end', 3000.0) is None
    assert chapter_trim_span([], '*x*', 'to_end', 3000.0) is None
    assert chapter_trim_span(CHAPTERS, None, 'to_end', 3000.0) is None
