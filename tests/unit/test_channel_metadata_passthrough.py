"""Tests for the iTunes + standard RSS channel-metadata passthrough.

Apple Podcasts and most podcast apps require several iTunes channel tags
(author, category, explicit, owner) to ingest a feed; without them apps
silently drop the feed or refuse to render artwork. Before this work the
served feed only emitted title/link/description/language/image at
channel scope, so feeds whose web-UI artwork looked fine still showed
no cover in subscribers' apps.
"""
import re

import defusedxml
defusedxml.defuse_stdlib()
from defusedxml.ElementTree import fromstring as _safe_fromstring

from rss_parser import RSSParser


ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _build_feed(channel_inner: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="{ITUNES_NS}"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Test Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <language>en</language>
    {channel_inner}
    <item>
      <title>Ep</title>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""


def _served(channel_inner: str) -> str:
    feed = _build_feed(channel_inner)
    served = RSSParser(base_url="https://mp.example.com").modify_feed(feed, "slug")
    first_item = served.find("<item>")
    return served[:first_item]


class TestItunesPassthrough:
    def test_itunes_author_passed_through(self):
        out = _served('<itunes:author>Adam Curry</itunes:author>')
        assert "<itunes:author>Adam Curry</itunes:author>" in out

    def test_itunes_summary_passed_through(self):
        out = _served('<itunes:summary>A daily news show.</itunes:summary>')
        assert "<itunes:summary>A daily news show.</itunes:summary>" in out

    def test_itunes_explicit_passed_through(self):
        out = _served('<itunes:explicit>no</itunes:explicit>')
        assert "<itunes:explicit>no</itunes:explicit>" in out

    def test_itunes_keywords_passed_through(self):
        out = _served('<itunes:keywords>podcasting,media</itunes:keywords>')
        assert "<itunes:keywords>podcasting,media</itunes:keywords>" in out

    def test_itunes_category_with_text_attribute(self):
        out = _served('<itunes:category text="News &amp; Politics"/>')
        # text= attribute survives, & is escaped.
        assert 'text="News &amp; Politics"' in out

    def test_multiple_itunes_categories(self):
        out = _served(
            '<itunes:category text="News &amp; Politics"/>'
            '<itunes:category text="Technology"/>'
        )
        assert out.count("<itunes:category") == 2

    def test_itunes_owner_with_nested_name_email(self):
        out = _served(
            '<itunes:owner>'
            '<itunes:name>Adam Curry</itunes:name>'
            '<itunes:email>adam@example.com</itunes:email>'
            '</itunes:owner>'
        )
        assert "<itunes:owner>" in out
        assert "<itunes:name>Adam Curry</itunes:name>" in out
        assert "<itunes:email>adam@example.com</itunes:email>" in out

    def test_itunes_type_passed_through(self):
        out = _served('<itunes:type>episodic</itunes:type>')
        assert "<itunes:type>episodic</itunes:type>" in out


class TestItunesNewFeedUrlStripped:
    def test_itunes_new_feed_url_must_not_leak(self):
        # CRITICAL: if this passes through, podcast apps interpret it as a
        # migration signal and move every MinusPod subscriber to the
        # upstream feed URL. Never carry through.
        out = _served(
            '<itunes:new-feed-url>https://upstream.example.com/migrate.xml</itunes:new-feed-url>'
        )
        assert "itunes:new-feed-url" not in out
        assert "migrate.xml" not in out


class TestStandardRssPassthrough:
    def test_managing_editor(self):
        out = _served('<managingEditor>editor@example.com (Editor)</managingEditor>')
        assert "<managingEditor>editor@example.com (Editor)</managingEditor>" in out

    def test_webmaster(self):
        out = _served('<webMaster>web@example.com</webMaster>')
        assert "<webMaster>web@example.com</webMaster>" in out

    def test_copyright(self):
        out = _served('<copyright>(c) 2026 Example</copyright>')
        assert "<copyright>" in out
        assert "2026" in out

    def test_standard_category(self):
        out = _served('<category>News</category>')
        assert "<category>News</category>" in out


class TestAlwaysEmitted:
    def test_generator_is_minuspod(self):
        out = _served("")
        assert "<generator>MinusPod</generator>" in out

    def test_lastbuilddate_is_fresh_and_rfc2822(self):
        out = _served("")
        m = re.search(r"<lastBuildDate>([^<]+)</lastBuildDate>", out)
        assert m is not None
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(m.group(1))
        # Should be a recent datetime, not from upstream (which set
        # 2026-05-15 in the upstream feed).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        assert abs((now - parsed).total_seconds()) < 60

    def test_upstream_lastbuilddate_is_not_carried_through(self):
        # Even when upstream supplies its own lastBuildDate we regenerate.
        upstream_stamp = "Wed, 01 Jan 2020 00:00:00 +0000"
        out = _served(f'<lastBuildDate>{upstream_stamp}</lastBuildDate>')
        assert upstream_stamp not in out


class TestServedFeedStillParses:
    def test_served_feed_with_full_metadata_parses_cleanly(self):
        feed = _build_feed(
            '<itunes:owner><itunes:name>X</itunes:name><itunes:email>x@y</itunes:email></itunes:owner>'
            '<itunes:author>A</itunes:author>'
            '<itunes:summary>S with &amp; ampersand</itunes:summary>'
            '<itunes:category text="News &amp; Politics"/>'
            '<itunes:explicit>no</itunes:explicit>'
            '<managingEditor>m@x.com</managingEditor>'
        )
        served = RSSParser(base_url="https://mp.example.com").modify_feed(feed, "slug")
        # Will raise if malformed
        _safe_fromstring(served.encode("utf-8"))
