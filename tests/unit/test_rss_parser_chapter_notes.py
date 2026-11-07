"""Served-feed descriptions carry the chapter list when enabled (#720)."""
import json

import pytest

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


def _block_feed(itunes_block, upstream_block=None):
    xml = UPSTREAM
    xml = xml.replace('<rss version="2.0">', '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">')
    if upstream_block:
        xml = xml.replace('<item>', f'<itunes:block>{upstream_block}</itunes:block><item>', 1)
    return RSSParser(base_url='https://mp.example.com').modify_feed(
        xml, 'show', itunes_block=itunes_block)


@pytest.mark.parametrize('flag, upstream, count, text', [
    (True, 'no', 1, 'Yes'),
    (True, None, 1, 'Yes'),
    (False, 'no', 1, 'no'),
    (False, None, 0, None),
])
def test_itunes_block_flag(flag, upstream, count, text):
    out = _block_feed(flag, upstream_block=upstream)
    assert out.count('<itunes:block>') == count
    if text:
        assert f'<itunes:block>{text}</itunes:block>' in out


def test_upstream_item_gets_the_block_when_enabled():
    assert '<p>Notes</p>\n\n' + BLOCK in _feed({UPSTREAM_ID: CHAPTERS})


def test_no_block_when_disabled_or_without_chapters():
    assert 'Chapters</p>' not in _feed(None)
    assert 'Chapters</p>' not in _feed({})


def test_db_appended_item_gets_the_block_too():
    extra = [{'episode_id': 'abcdef012345', 'title': 'Old', 'description': '<p>Old notes</p>',
              'published_at': '2026-01-01T00:00:00Z', 'new_duration': 100, 'episode_number': 1}]
    assert '<p>Old notes</p>\n\n' + BLOCK in _feed({'abcdef012345': CHAPTERS}, extra)


def test_db_appended_item_without_notes_still_gets_a_description():
    extra = [{'episode_id': 'abcdef012345', 'title': 'Old', 'description': None,
              'published_at': '2026-01-01T00:00:00Z', 'new_duration': 100, 'episode_number': 1}]
    assert '<description><![CDATA[' + BLOCK in _feed({'abcdef012345': CHAPTERS}, extra)


# Review links

LINK_BASE = 'http://localhost:8000/ui/feeds/show/episodes/'


def _processed_extra(episode_id, description='<p>Old notes</p>'):
    return [{'episode_id': episode_id, 'title': 'Old', 'description': description,
             'published_at': '2026-01-01T00:00:00Z', 'new_duration': 100, 'episode_number': 1}]


def _links_feed(extra, review_link_ids=None, chapter_notes=None, xml=UPSTREAM, **kwargs):
    parser = RSSParser(base_url='https://mp.example.com')
    return parser.modify_feed(xml, 'show', extra_episodes=extra, chapter_notes=chapter_notes,
                              review_link_ids=review_link_ids, **kwargs)


def test_review_links_off_by_default():
    parser = RSSParser(base_url='https://mp.example.com')
    xml = parser.modify_feed(UPSTREAM, 'show', extra_episodes=_processed_extra(UPSTREAM_ID))
    assert 'Review in MinusPod' not in xml


def test_upstream_processed_item_gets_the_links():
    xml = _links_feed(_processed_extra(UPSTREAM_ID), {UPSTREAM_ID})
    assert f'<p>Notes</p>\n\n<p><a href="{LINK_BASE}{UPSTREAM_ID}">Review in MinusPod</a>' in xml
    assert f'{LINK_BASE}{UPSTREAM_ID}?rate=5">Rate 5 stars' in xml


def test_unknown_upstream_item_gets_no_links():
    assert 'Review in MinusPod' not in _links_feed([], review_link_ids=set())


def test_still_processing_upstream_item_gets_the_links():
    xml = _links_feed([], {UPSTREAM_ID})
    assert f'<p><a href="{LINK_BASE}{UPSTREAM_ID}">Review in MinusPod' in xml


def test_db_appended_item_gets_the_links():
    xml = _links_feed(_processed_extra('abcdef012345'), {'abcdef012345'})
    assert f'<p>Old notes</p>\n\n<p><a href="{LINK_BASE}abcdef012345">Review in MinusPod' in xml


def test_links_follow_the_chapter_block():
    xml = _links_feed(_processed_extra('abcdef012345'), {'abcdef012345'}, chapter_notes={'abcdef012345': CHAPTERS})
    assert BLOCK + '\n\n<p><a href=' in xml
    assert xml.index('Chapters</p>') < xml.index('Review in MinusPod')


# content:encoded show notes (#969)

NOTES_FEED = UPSTREAM.replace(
    '<rss version="2.0">',
    '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
    ' xmlns:content="http://purl.org/rss/1.0/modules/content/">',
).replace(
    '<description><![CDATA[<p>Notes</p>]]></description>',
    '<itunes:summary>Short summary</itunes:summary>\n'
    '<content:encoded><![CDATA[<p>05:00 Topic</p><script>x()</script>]]></content:encoded>',
)
CUTS = {UPSTREAM_ID: '[{"start": 60.0, "end": 120.0, "replacement_duration": 0.0}]'}
DB_ID = 'abcdef012345'


def _db_content_extra():
    extra = _processed_extra(DB_ID)
    extra[0]['content_encoded'] = '<p>05:00 Full</p>'
    return extra


def test_summary_stays_in_description_and_notes_go_to_content_encoded():
    xml = _links_feed([], {UPSTREAM_ID}, {UPSTREAM_ID: CHAPTERS}, xml=NOTES_FEED, applied_cuts=CUTS)
    assert 'xmlns:content="http://purl.org/rss/1.0/modules/content/"' in xml
    assert '<description><![CDATA[Short summary' in xml
    content = xml.split('<content:encoded><![CDATA[')[1].split(']]></content:encoded>')[0]
    assert '<p>04:00 Topic</p>' in content
    assert BLOCK in content
    assert 'Review in MinusPod' in content
    assert '<script' not in content


def test_summary_description_and_content_encoded_keep_full_notes():
    feed = NOTES_FEED.replace(
        '<content:encoded>', '<description>Plain desc</description>\n<content:encoded>')
    xml = _links_feed([], xml=feed)
    assert '<description><![CDATA[Short summary' in xml
    assert 'Plain desc' not in xml
    assert '<content:encoded><![CDATA[<p>05:00 Topic</p>' in xml


def test_no_content_encoded_when_notes_are_the_description():
    feed = NOTES_FEED.replace('<itunes:summary>Short summary</itunes:summary>\n', '')
    xml = _links_feed([], xml=feed)
    assert '<content:encoded>' not in xml
    assert '<p>05:00 Topic</p>' in xml


def test_db_appended_item_content_encoded_gets_chapters_and_links():
    xml = _links_feed(_db_content_extra(), {DB_ID}, {DB_ID: CHAPTERS})
    assert '<content:encoded><![CDATA[<p>05:00 Full</p>' in xml
    content = xml.split('<content:encoded><![CDATA[')[1].split(']]></content:encoded>')[0]
    assert BLOCK in content
    assert 'Review in MinusPod' in content


def test_db_appended_item_content_encoded_shifts_for_cuts():
    xml = _links_feed(_db_content_extra(), applied_cuts={DB_ID: CUTS[UPSTREAM_ID]})
    assert '<content:encoded><![CDATA[<p>04:00 Full</p>' in xml
    assert xml.count('<content:encoded>') == 1
