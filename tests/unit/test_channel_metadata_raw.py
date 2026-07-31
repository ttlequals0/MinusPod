"""Tests for channel metadata read from raw <channel> children (#596).

feedparser flattens channel-level containers it does not recognise. A
Podcasting 2.0 <podcast:liveItem> carries its own title/description/link, so
the live episode's blurb ends up reported as the show's description and its
chat link as the show's website.
"""
import defusedxml
defusedxml.defuse_stdlib()

from rss_parser import RSSParser


def _feed(channel_inner: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>REAL SHOW</title>
    <description>REAL DESCRIPTION</description>
    <link>https://real.example/</link>
    <language>en-us</language>
    {channel_inner}
    <item><title>Ep 1</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/></item>
  </channel>
</rss>"""


_LIVE_ITEM = """
    <podcast:liveItem status="live">
      <title>LIVE EPISODE</title>
      <description>LIVE EPISODE BLURB</description>
      <link>https://live.example/chat</link>
      <itunes:summary>LIVE SUMMARY</itunes:summary>
    </podcast:liveItem>
"""


class TestLiveItemDoesNotLeak:
    def test_description_is_the_shows_not_the_live_episodes(self):
        meta = RSSParser.extract_channel_metadata(_feed(_LIVE_ITEM))
        assert meta['description'] == 'REAL DESCRIPTION'

    def test_link_is_the_shows_not_the_live_chat(self):
        meta = RSSParser.extract_channel_metadata(_feed(_LIVE_ITEM))
        assert meta['link'] == 'https://real.example/'

    def test_resolver_agrees_with_feedparser_free_result(self):
        parser = RSSParser()
        content = _feed(_LIVE_ITEM)
        fields = parser.resolve_channel_fields(
            content, parsed_feed=parser.parse_feed(content))
        assert fields['description'] == 'REAL DESCRIPTION'
        assert fields['link'] == 'https://real.example/'
        assert fields['title'] == 'REAL SHOW'
        assert fields['language'] == 'en-us'

    def test_feedparser_alone_is_corrupted(self):
        """Guards the premise: without the raw read these values are wrong."""
        parsed = RSSParser().parse_feed(_feed(_LIVE_ITEM))
        assert parsed.feed.get('description') != 'REAL DESCRIPTION'
        assert parsed.feed.get('link') == 'https://live.example/chat'

    def test_served_feed_carries_the_shows_metadata(self):
        parser = RSSParser()
        out = parser.modify_feed(_feed(_LIVE_ITEM), 'a-slug')
        assert '<description><![CDATA[REAL DESCRIPTION]]></description>' in out
        assert '<link>https://real.example/</link>' in out
        assert 'LIVE EPISODE BLURB' not in out
        assert 'https://live.example/chat' not in out


class TestAuthorAndCategories:
    """A live item's author and categories flatten into the channel too."""

    FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>REAL SHOW</title>
  <itunes:author>REAL AUTHOR</itunes:author>
  <category>RealCat</category>
  <itunes:category text="Technology"><itunes:category text="Gadgets"/></itunes:category>
  <podcast:liveItem status="live">
    <title>LIVE</title>
    <itunes:author>LIVE AUTHOR</itunes:author>
    <category>LiveCat</category>
    <itunes:category text="Comedy"/>
  </podcast:liveItem>
  <item><title>Ep</title></item>
</channel></rss>"""

    def _fields(self):
        parser = RSSParser()
        return parser.resolve_channel_fields(
            self.FEED, parsed_feed=parser.parse_feed(self.FEED))

    def test_author_is_the_shows(self):
        assert self._fields()['author'] == 'REAL AUTHOR'

    def test_categories_exclude_the_live_items(self):
        cats = self._fields()['categories']
        assert 'RealCat' in cats and 'Technology' in cats and 'Gadgets' in cats
        assert 'LiveCat' not in cats and 'Comedy' not in cats

    def test_feedparser_alone_is_corrupted(self):
        parsed = RSSParser().parse_feed(self.FEED)
        assert parsed.feed.get('author') == 'LIVE AUTHOR'
        assert 'LiveCat' in [t.get('term') for t in parsed.feed.get('tags', [])]

    def test_managing_editor_is_the_author_fallback(self):
        feed = """<?xml version="1.0"?><rss version="2.0"><channel>
          <title>T</title><managingEditor>ed@example.com (Ed)</managingEditor>
          </channel></rss>"""
        parser = RSSParser()
        fields = parser.resolve_channel_fields(
            feed, parsed_feed=parser.parse_feed(feed))
        assert fields['author'] == 'ed@example.com (Ed)'


class TestNamespacedSiblings:
    def test_atom_self_link_is_not_taken_as_the_website(self):
        # <atom:link rel="self"> is a near-universal first child of <channel>.
        meta = RSSParser.extract_channel_metadata(_feed(
            '<atom:link rel="self" href="https://self.example/feed.xml"/>'))
        assert meta['link'] == 'https://real.example/'

    def test_itunes_summary_is_kept_separate_from_description(self):
        meta = RSSParser.extract_channel_metadata(_feed(
            '<itunes:summary>ITUNES SUMMARY</itunes:summary>'))
        assert meta['description'] == 'REAL DESCRIPTION'
        assert meta['itunes_summary'] == 'ITUNES SUMMARY'


class TestDescriptionMarkup:
    def test_inline_html_survives(self):
        # elem.text alone stops at the first child element.
        feed = """<?xml version="1.0"?><rss version="2.0"><channel>
          <title>T</title>
          <description>Intro <b>bold</b> and <a href="http://x&amp;y">l</a> tail</description>
          </channel></rss>"""
        assert RSSParser.extract_channel_metadata(feed)['description'] == (
            'Intro <b>bold</b> and <a href="http://x&amp;y">l</a> tail')

    def test_cdata_is_returned_verbatim(self):
        feed = """<?xml version="1.0"?><rss version="2.0"><channel>
          <title>T</title>
          <description><![CDATA[<p>Wrapped</p>]]></description>
          </channel></rss>"""
        assert RSSParser.extract_channel_metadata(feed)['description'] == '<p>Wrapped</p>'


class TestFallbackWhenNoChannel:
    ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Show</title><subtitle>Atom sub</subtitle>
  <link href="https://atom.example/"/>
</feed>"""

    def test_atom_has_no_channel(self):
        assert RSSParser.extract_channel_metadata(self.ATOM) is None

    def test_resolver_falls_back_to_feedparser_for_atom(self):
        parser = RSSParser()
        fields = parser.resolve_channel_fields(
            self.ATOM, parsed_feed=parser.parse_feed(self.ATOM))
        assert fields['title'] == 'Atom Show'
        assert fields['description'] == 'Atom sub'
        assert fields['link'] == 'https://atom.example/'

    def test_malformed_xml_returns_none(self):
        assert RSSParser.extract_channel_metadata(b'<rss><chan') is None

    def test_empty_input_returns_none(self):
        assert RSSParser.extract_channel_metadata('') is None

    def test_accepts_bytes(self):
        meta = RSSParser.extract_channel_metadata(_feed('').encode('utf-8'))
        assert meta['description'] == 'REAL DESCRIPTION'


class TestDescriptionFallbackChain:
    def test_itunes_summary_used_when_description_absent(self):
        feed = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>T</title>
  <itunes:summary>ONLY SUMMARY</itunes:summary></channel></rss>"""
        parser = RSSParser()
        fields = parser.resolve_channel_fields(
            feed, parsed_feed=parser.parse_feed(feed))
        assert fields['description'] == 'ONLY SUMMARY'

    def test_itunes_subtitle_used_when_nothing_else(self):
        feed = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>T</title>
  <itunes:subtitle>ONLY SUBTITLE</itunes:subtitle></channel></rss>"""
        parser = RSSParser()
        fields = parser.resolve_channel_fields(
            feed, parsed_feed=parser.parse_feed(feed))
        assert fields['description'] == 'ONLY SUBTITLE'
