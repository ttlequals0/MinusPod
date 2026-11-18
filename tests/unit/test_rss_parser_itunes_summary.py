"""Item description, content:encoded and itunes:summary keep their own values (#1032)."""
import json

import feedparser
import pytest

from tests.app_bootstrap import bootstrap
from tests.unit.db_test_utils import assert_column_migrated

bootstrap('rss_itunes_summary_test_')

from rss_parser import RSSParser  # noqa: E402

NS = ('xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
      'xmlns:content="http://purl.org/rss/1.0/modules/content/"')
DESC = '<description>The real description</description>'
SUM = '<itunes:summary>Plain summary</itunes:summary>'
CE = '<content:encoded><![CDATA[<p>Full notes</p>]]></content:encoded>'


def _feed(*item_parts, root_attrs=NS):
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" {root_attrs}><channel>'
            '<title>The Daily Tech Show</title><link>https://example.com</link>'
            '<description>d</description>'
            f'<item><title>Ep 1</title>{"".join(item_parts)}<guid>g1</guid>'
            '<enclosure url="https://example.com/1.mp3" type="audio/mpeg" length="1"/></item>'
            '</channel></rss>')


def _served(xml):
    return RSSParser(base_url='https://mp.example.com').modify_feed(xml, 'show')


def _episode(xml):
    return RSSParser().extract_episodes(xml)[0]


def test_description_and_itunes_summary_without_content_encoded():
    xml = _feed(DESC, SUM)
    out = _served(xml)
    assert '<description><![CDATA[The real description' in out
    assert '<itunes:summary>Plain summary</itunes:summary>' in out
    assert '<content:encoded>' not in out
    ep = _episode(xml)
    assert (ep['description'], ep['content_encoded'], ep['itunes_summary']) == (
        'The real description', '', 'Plain summary')


@pytest.mark.parametrize('parts', [
    (DESC, SUM), (SUM, DESC), (SUM, DESC, CE), (DESC, CE, SUM), (SUM, CE, DESC), (CE, DESC, SUM)])
def test_each_field_keeps_its_own_value_in_any_order(parts):
    xml = _feed(*parts)
    has_content = CE in parts
    out = _served(xml)
    assert '<description><![CDATA[The real description' in out
    assert '<itunes:summary>Plain summary</itunes:summary>' in out
    assert out.count('<content:encoded>') == (1 if has_content else 0)
    ep = _episode(xml)
    assert (ep['description'], ep['content_encoded'], ep['itunes_summary']) == (
        'The real description', '<p>Full notes</p>' if has_content else '', 'Plain summary')


@pytest.mark.parametrize('extra', [(), (SUM,)])
def test_content_encoded_equal_to_description_is_not_repeated(extra):
    same = '<p>Same notes</p>'
    xml = _feed(f'<description><![CDATA[{same}]]></description>',
                f'<content:encoded><![CDATA[{same}]]></content:encoded>', *extra)
    assert '<content:encoded>' not in _served(xml)
    assert _episode(xml)['content_encoded'] == ''


def test_only_content_encoded_is_the_description():
    xml = _feed(CE)
    ep = _episode(xml)
    assert ep['description'] == '<p>Full notes</p>'
    assert ep['content_encoded'] == ''
    out = _served(xml)
    assert '<content:encoded>' not in out
    assert '<itunes:summary>' not in out


def test_only_itunes_summary_is_description_and_still_emitted():
    xml = _feed(SUM)
    ep = _episode(xml)
    assert (ep['description'], ep['content_encoded'], ep['itunes_summary']) == (
        'Plain summary', '', 'Plain summary')
    out = _served(xml)
    assert '<description><![CDATA[Plain summary' in out
    assert '<itunes:summary>Plain summary</itunes:summary>' in out
    assert '<content:encoded>' not in out


def test_itunes_summary_is_xml_escaped_and_not_decorated():
    out = _served(_feed(DESC, '<itunes:summary>Q &amp; A &lt;live&gt;</itunes:summary>'))
    assert '<itunes:summary>Q &amp; A &lt;live&gt;</itunes:summary>' in out


def test_alignment_uses_the_full_entry_list_when_capped():
    items = ''.join(
        f'<item><title>E{i}</title>{DESC}<itunes:summary>S{i}</itunes:summary>'
        f'<guid>g{i}</guid><enclosure url="https://example.com/{i}.mp3" type="audio/mpeg"/></item>'
        for i in range(3))
    xml = (f'<?xml version="1.0"?><rss version="2.0" {NS}><channel><title>T</title>'
           f'<link>https://example.com</link><description>d</description>{items}</channel></rss>')
    out = RSSParser(base_url='https://mp.example.com').modify_feed(xml, 'show', max_episodes=2)
    assert '<itunes:summary>S0</itunes:summary>' in out
    assert '<itunes:summary>S1</itunes:summary>' in out
    assert 'S2' not in out


def test_atom_feed_is_unchanged():
    atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>'
            '<entry><title>Ep</title><id>g1</id><summary>Atom summary</summary>'
            '<content type="html">&lt;p&gt;Atom body&lt;/p&gt;</content>'
            '<link rel="enclosure" href="https://example.com/1.mp3" type="audio/mpeg"/></entry></feed>')
    ep = _episode(atom)
    assert ep['description'] == 'Atom summary'
    assert ep['content_encoded'] == '<p>Atom body</p>'
    assert ep['itunes_summary'] == ''


def test_db_appended_item_emits_stored_itunes_summary():
    parser = RSSParser(base_url='https://mp.example.com')
    ep = {'episode_id': 'abcdef012345', 'title': 'Ep', 'description': 'd',
          'itunes_summary': 'Stored <summary>', 'published_at': '2024-01-01T00:00:00Z'}
    lines = []
    parser._append_db_episode_item(lines, 'show', ep, None)
    assert '  <itunes:summary>Stored &lt;summary&gt;</itunes:summary>' in lines
    ep['itunes_summary'] = '  '
    lines = []
    parser._append_db_episode_item(lines, 'show', ep, None)
    assert not any('itunes:summary' in line for line in lines)


def test_migration_adds_itunes_summary_column(temp_db):
    assert_column_migrated(temp_db, 'episodes', 'itunes_summary')


def _discover(temp_db, **fields):
    temp_db.create_podcast('show', 'https://example.com/feed.xml', 'Show')
    ep = {'id': 'e1', 'url': 'https://example.com/1.mp3', 'title': 'Ep', 'description': 'd',
          'published': 'Mon, 01 Jan 2024 00:00:00 GMT', 'tags': [], **fields}
    temp_db.bulk_upsert_discovered_episodes('show', [ep])
    return ep


def _row(temp_db):
    return temp_db.get_connection().execute(
        "SELECT description, content_encoded, itunes_summary FROM episodes WHERE episode_id = 'e1'"
    ).fetchone()


def test_upsert_heals_content_encoded_written_from_itunes_summary(temp_db):
    ep = _discover(temp_db, content_encoded='Plain summary')
    assert temp_db.get_episode('show', 'e1')['content_encoded'] == 'Plain summary'
    ep.update(content_encoded='', itunes_summary='Plain summary')
    temp_db.bulk_upsert_discovered_episodes('show', [ep])
    row = temp_db.get_episode('show', 'e1')
    assert row['content_encoded'] is None
    assert row['itunes_summary'] == 'Plain summary'


def test_upsert_keeps_a_legitimate_content_encoded(temp_db):
    ep = _discover(temp_db, content_encoded='<p>Full notes</p>')
    ep.update(content_encoded='', itunes_summary='Plain summary')
    temp_db.bulk_upsert_discovered_episodes('show', [ep])
    row = temp_db.get_episode('show', 'e1')
    assert row['content_encoded'] == '<p>Full notes</p>'
    assert row['itunes_summary'] == 'Plain summary'


def _hide_patterns(xml, patterns):
    return RSSParser(base_url='https://mp.example.com').modify_feed(
        xml, 'show', hide_filters={'description_skip_patterns': json.dumps(patterns)})


def test_hide_description_filter_uses_the_real_description():
    xml = _feed(SUM, DESC)
    assert '<item>' in _hide_patterns(xml, ['*Plain summary*'])
    assert '<item>' not in _hide_patterns(xml, ['*real description*'])


@pytest.mark.parametrize('stored, incoming, expected', [
    # second variant: stored description is the summary, content is the real description
    (dict(description='Plain summary', content_encoded='The real description'),
     dict(description='The real description', content_encoded='', itunes_summary='Plain summary'),
     ('The real description', None, 'Plain summary')),
    (dict(description='Plain summary', content_encoded='<p>Other notes</p>'),
     dict(description='The real description', content_encoded='', itunes_summary='Plain summary'),
     ('The real description', '<p>Other notes</p>', 'Plain summary')),
    (dict(description='Same text'),
     dict(itunes_summary='Same text'),
     ('Same text', None, 'Same text')),
    (dict(description='Plain summary', content_encoded='<p>Full notes</p>'),
     dict(description='The real description', itunes_summary='Plain summary'),
     ('The real description', '<p>Full notes</p>', 'Plain summary')),
], ids=['heals', 'keeps-differing-content', 'identical-untouched', 'keeps-real-content'])
def test_upsert_second_variant(temp_db, stored, incoming, expected):
    ep = _discover(temp_db, **stored)
    ep.update(incoming)
    temp_db.bulk_upsert_discovered_episodes('show', [ep])
    assert tuple(_row(temp_db)) == expected


@pytest.mark.parametrize('real', ['The real description', '<p>Real <b>html</b> description</p>'])
def test_content_heal_fires_for_a_row_from_the_old_code_path(temp_db, real):
    xml = _feed(SUM, f'<description><![CDATA[{real}]]></description>')
    entry = feedparser.parse(xml).entries[0]
    old_description = RSSParser._get_episode_description(entry)
    old_content = RSSParser._get_episode_content(entry, old_description)
    assert old_description == 'Plain summary'
    ep = _discover(temp_db, description=old_description, content_encoded=old_content)
    new = _episode(xml)
    ep.update(description=new['description'], content_encoded=new['content_encoded'],
              itunes_summary=new['itunes_summary'])
    temp_db.bulk_upsert_discovered_episodes('show', [ep])
    row = _row(temp_db)
    assert row['description'] == new['description']
    assert row['content_encoded'] is None
