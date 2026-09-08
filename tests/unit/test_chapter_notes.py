"""Chapter list appended to episode descriptions (#720)."""
import json
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('chapter_notes_test_')

from chapter_notes import append_chapters, format_chapter_block  # noqa: E402
from config import resolve_chapters_in_notes  # noqa: E402

CHAPTERS = json.dumps({'version': '1.2.0', 'chapters': [
    {'startTime': 0, 'title': 'Intro'},
    {'startTime': 252.4, 'title': 'Views & metrics'},
    {'startTime': 3760, 'title': 'Outro'},
]})
BLOCK = '<p>Chapters</p><p>00:00 Intro<br>04:12 Views &amp; metrics<br>1:02:40 Outro</p>'


def test_block_lists_one_chapter_per_line_with_timestamps():
    assert format_chapter_block(CHAPTERS) == BLOCK


def test_block_is_empty_without_chapters():
    assert format_chapter_block(None) == ''
    assert format_chapter_block(json.dumps({'version': '1.2.0', 'chapters': []})) == ''
    assert format_chapter_block('not json') == ''


def test_malformed_entries_are_skipped_not_fatal():
    raw = json.dumps({'chapters': [None, {'startTime': None, 'title': 'x'},
                                   {'startTime': 5, 'title': 'ok'}]})
    assert format_chapter_block(raw) == '<p>Chapters</p><p>00:05 ok</p>'
    assert format_chapter_block(json.dumps({'chapters': [None]})) == ''


def test_append_keeps_the_description_and_adds_the_block():
    assert append_chapters('<p>Show notes</p>', CHAPTERS) == '<p>Show notes</p>' + BLOCK
    assert append_chapters('<p>Show notes</p>', None) == '<p>Show notes</p>'
    assert append_chapters(None, CHAPTERS) == BLOCK


def test_ad_chapter_lines_carry_category_suffix():
    chapters = json.dumps({'version': '1.2.0', 'chapters': [
        {'startTime': 0, 'title': 'Intro'},
        {'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'},
        {'startTime': 960, 'title': 'Show', 'kind': 'resume'},
        {'startTime': 1200, 'title': '[mp:cross_promo?]', 'kind': 'ad',
         'category': 'cross_promo', 'held': True},
    ]})
    assert format_chapter_block(chapters) == (
        '<p>Chapters</p><p>00:00 Intro<br>15:00 [mp:sponsor] (Sponsor)<br>16:00 Show'
        '<br>20:00 [mp:cross_promo?] (Cross-promo, awaiting review)</p>')


def test_ad_chapter_without_a_known_category_falls_back():
    chapters = json.dumps({'chapters': [
        {'startTime': 30, 'title': 'A', 'kind': 'ad'},
        {'startTime': 60, 'title': 'B', 'kind': 'ad', 'category': 'mystery'},
    ]})
    assert format_chapter_block(chapters) == (
        '<p>Chapters</p><p>00:30 A (Ad)<br>01:00 B (mystery)</p>')


def _db(global_value):
    db = MagicMock()
    db.get_setting_bool.return_value = global_value
    return db


def test_resolver_uses_the_global_setting_without_an_override():
    assert resolve_chapters_in_notes(_db(True), {'chapters_in_notes': None}) is True
    assert resolve_chapters_in_notes(_db(False), {}) is False
    assert resolve_chapters_in_notes(_db(True), None) is True


def test_resolver_lets_a_feed_override_win():
    assert resolve_chapters_in_notes(_db(False), {'chapters_in_notes': 'on'}) is True
    assert resolve_chapters_in_notes(_db(True), {'chapters_in_notes': 'off'}) is False
