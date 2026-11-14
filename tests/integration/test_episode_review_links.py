"""The served feed carries review links on every episode MinusPod knows about, when enabled."""
import pytest

from api import get_database
from rss_parser import RSSParser

SLUG = 'review-links-slug'
UPSTREAM = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Show</title><link>https://u.example.com</link>
<description>d</description>
<item><title>Ep 1</title><description><![CDATA[<p>Notes</p>]]></description>
<guid>g1</guid><enclosure url="https://u.example.com/1.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Ep 2</title><description><![CDATA[<p>Pending</p>]]></description>
<guid>g2</guid><enclosure url="https://u.example.com/2.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Ep 3</title><description><![CDATA[<p>Unknown</p>]]></description>
<guid>g3</guid><enclosure url="https://u.example.com/3.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Ep 4</title><description><![CDATA[<p>Failed</p>]]></description>
<guid>g4</guid><enclosure url="https://u.example.com/4.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""


@pytest.fixture
def seeded(app_client):
    db = get_database()
    db.set_setting('review_links_in_notes', 'false', is_default=True)
    db.create_podcast(SLUG, 'https://u.example.com/feed.xml', 'Review Links')
    db.upsert_episode(slug=SLUG, episode_id=RSSParser.generate_episode_id('https://u.example.com/1.mp3', 'g1'),
                      original_url='https://u.example.com/1.mp3', title='Ep 1',
                      description='<p>Notes</p>', status='processed',
                      processed_file='episodes/ep1.mp3')
    db.upsert_episode(slug=SLUG, episode_id=RSSParser.generate_episode_id('https://u.example.com/2.mp3', 'g2'),
                      original_url='https://u.example.com/2.mp3', title='Ep 2',
                      description='<p>Pending</p>', status='processing')
    db.upsert_episode(slug=SLUG, episode_id=RSSParser.generate_episode_id('https://u.example.com/4.mp3', 'g4'),
                      original_url='https://u.example.com/4.mp3', title='Ep 4',
                      description='<p>Failed</p>', status='failed')
    yield db
    db.set_setting('review_links_in_notes', 'false', is_default=True)
    db.delete_podcast(SLUG)


def _served(db):
    import main_app.feeds as mf
    podcast = db.get_podcast_by_slug(SLUG)
    parsed = RSSParser().parse_feed(UPSTREAM)
    mf._build_and_save_served_rss(SLUG, UPSTREAM, parsed, podcast)
    return mf.storage.get_rss(SLUG)


def test_no_links_by_default(seeded):
    assert 'Review in MinusPod' not in _served(seeded)


def test_global_toggle_adds_links_to_every_known_episode(seeded):
    seeded.set_setting('review_links_in_notes', 'true', is_default=False)
    xml = _served(seeded)
    assert xml.count('Review in MinusPod') == 3
    assert '<p>Notes</p>\n\n<p><a href=' in xml
    assert '<p>Pending</p>\n\n<p><a href=' in xml
    assert '<p>Failed</p>\n\n<p><a href=' in xml
    assert '<p>Unknown</p>]]>' in xml


def test_feed_override_off_wins_over_global_on(seeded):
    seeded.set_setting('review_links_in_notes', 'true', is_default=False)
    seeded.update_podcast(SLUG, review_links_in_notes='off')
    assert 'Review in MinusPod' not in _served(seeded)
