"""Per-feed title override (#375).

A MinusPod-processed feed otherwise shows the exact source title in podcast
apps, so it's easy to tap the ad-laden original. title_override lets a user
rename a feed; the rename must actually replace the channel title in the
SERVED RSS, survive RSS refreshes, and fall back to the source title when
cleared.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database
from rss_parser import RSSParser

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _feed():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Source Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    <image>
      <url>https://example.com/art.png</url>
      <title>Source Show</title>
      <link>https://example.com</link>
    </image>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


def _serve(title_override=None):
    return RSSParser(base_url="https://mp.example.com").modify_feed(
        _feed(), "slug", title_override=title_override)


class TestServedFeedTitle:

    def test_override_replaces_channel_title(self):
        served = _serve(title_override="My Show (MP)")
        assert "<title>My Show (MP)</title>" in served
        # source channel title fully gone from the channel scope
        channel = served[:served.find("<item>")]
        assert "Source Show" not in channel

    def test_override_replaces_image_title_too(self):
        served = _serve(title_override="My Show (MP)")
        # both the channel <title> and the <image><title> carry the override
        assert served.count("<title>My Show (MP)</title>") >= 2

    def test_no_override_uses_source_title(self):
        served = _serve()
        assert "<title>Source Show</title>" in served

    def test_override_does_not_touch_episode_titles(self):
        served = _serve(title_override="My Show (MP)")
        assert "<title>Ep One</title>" in served

    def test_override_is_xml_escaped(self):
        served = _serve(title_override="Tom & Jerry <news>")
        assert "Tom &amp; Jerry &lt;news&gt;" in served

    def test_served_feed_with_special_title_is_well_formed(self):
        import defusedxml.ElementTree as ET
        served = _serve(title_override="A & B <C> \"D\" ']]>")
        # must parse without raising -- the override cannot break the document
        root = ET.fromstring(served)
        assert root.find('channel/title').text == "A & B <C> \"D\" ']]>"


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


class TestTitleOverridePersistence:

    def test_set_and_clear_override(self, db):
        db.create_podcast('show', 'https://example.com/feed.xml', title='Source Show')
        db.update_podcast('show', title_override='My Show (MP)')
        assert db.get_podcast_by_slug('show')['title_override'] == 'My Show (MP)'
        db.update_podcast('show', title_override=None)
        assert db.get_podcast_by_slug('show')['title_override'] is None

    def test_refresh_updating_source_title_does_not_touch_override(self, db):
        db.create_podcast('show', 'https://example.com/feed.xml', title='Source Show')
        db.update_podcast('show', title_override='My Show (MP)')
        # simulate an RSS refresh re-writing the source title
        db.update_podcast('show', title='Source Show Renamed Upstream')
        row = db.get_podcast_by_slug('show')
        assert row['title'] == 'Source Show Renamed Upstream'
        assert row['title_override'] == 'My Show (MP)'
