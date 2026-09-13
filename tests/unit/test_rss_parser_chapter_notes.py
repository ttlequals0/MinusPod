"""Served-feed descriptions carry the chapter list when enabled (#720)."""
import json

from tests.app_bootstrap import bootstrap

bootstrap('rss_chapter_notes_test_')

from rss_parser import RSSParser  # noqa: E402

CHAPTERS = json.dumps({'version': '1.2.0', 'chapters': [
    {'startTime': 0, 'title': 'Intro'}, {'startTime': 90, 'title': 'News'}]})
BLOCK = '<p>Chapters</p><p>00:00 Intro<br>01:30 News</p>'
UPSTREAM_ID = RSSParser.generate_episode_id('https://u.example.com/1.mp3', 'g1')

UPSTREAM = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Show</title><link>https://u.example.com</link>
<description>d</description>
<item><title>Ep 1</title><description><![CDATA[<p>Notes</p>]]></description>
<guid>g1</guid><enclosure url="https://u.example.com/1.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""


def _feed(chapter_notes, extra=None):
    parser = RSSParser(base_url='https://mp.example.com')
    return parser.modify_feed(UPSTREAM, 'show', chapter_notes=chapter_notes,
                              extra_episodes=extra or [])


def test_upstream_item_gets_the_block_when_enabled():
    assert '<p>Notes</p>' + BLOCK in _feed({UPSTREAM_ID: CHAPTERS})


def test_no_block_when_disabled_or_without_chapters():
    assert 'Chapters</p>' not in _feed(None)
    assert 'Chapters</p>' not in _feed({})


def test_db_appended_item_gets_the_block_too():
    extra = [{'episode_id': 'abcdef012345', 'title': 'Old', 'description': '<p>Old notes</p>',
              'published_at': '2026-01-01T00:00:00Z', 'new_duration': 100, 'episode_number': 1}]
    assert '<p>Old notes</p>' + BLOCK in _feed({'abcdef012345': CHAPTERS}, extra)


def test_db_appended_item_without_notes_still_gets_a_description():
    extra = [{'episode_id': 'abcdef012345', 'title': 'Old', 'description': None,
              'published_at': '2026-01-01T00:00:00Z', 'new_duration': 100, 'episode_number': 1}]
    assert '<description><![CDATA[' + BLOCK in _feed({'abcdef012345': CHAPTERS}, extra)
