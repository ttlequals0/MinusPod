"""Preserve multiple transcript tags through a raw XML pass because feedparser keeps one."""
from rss_parser import RSSParser


def _feed(item_extra: str = '') -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Transcript Show</title>
    <item>
      <title>Ep One</title>
      <guid>ep-one</guid>
      <enclosure url="https://example.com/one.mp3" type="audio/mpeg"/>
      {item_extra}
    </item>
  </channel>
</rss>"""


class TestExtractEpisodesCapturesUpstreamTranscript:
    def test_picks_best_type_among_several_tags(self):
        feed = _feed(
            '<podcast:transcript url="https://upstream.example.com/ep1.html" type="text/html"/>'
            '<podcast:transcript url="https://upstream.example.com/ep1.json" type="application/json"/>'
            '<podcast:transcript url="https://upstream.example.com/ep1.vtt" type="text/vtt"/>'
            '<podcast:transcript url="https://upstream.example.com/ep1.srt" type="application/srt"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.vtt'
        assert episodes[0]['upstream_transcript_type'] == 'text/vtt'

    def test_type_matched_case_insensitively(self):
        feed = _feed(
            '<podcast:transcript url="https://upstream.example.com/ep1.srt" type="Application/SRT"/>'
            '<podcast:transcript url="https://upstream.example.com/ep1.txt" type="text/plain"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.srt'
        assert episodes[0]['upstream_transcript_type'] == 'application/srt'

    def test_single_tag_is_used(self):
        feed = _feed(
            '<podcast:transcript url="https://upstream.example.com/ep1.txt" type="text/plain"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.txt'
        assert episodes[0]['upstream_transcript_type'] == 'text/plain'

    def test_none_when_tag_absent(self):
        episodes = RSSParser().extract_episodes(_feed())
        assert episodes[0]['upstream_transcript_url'] is None
        assert episodes[0]['upstream_transcript_type'] is None

    def test_non_http_scheme_ignored(self):
        feed = _feed(
            '<podcast:transcript url="javascript:alert(1)" type="text/vtt"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] is None
        assert episodes[0]['upstream_transcript_type'] is None

    def test_non_http_tag_skipped_in_favor_of_http_tag(self):
        feed = _feed(
            '<podcast:transcript url="javascript:alert(1)" type="text/vtt"/>'
            '<podcast:transcript url="https://upstream.example.com/ep1.html" type="text/html"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.html'
        assert episodes[0]['upstream_transcript_type'] == 'text/html'

    def test_matches_item_by_enclosure_when_guid_absent(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Transcript Show</title>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/one.mp3" type="audio/mpeg"/>
      <podcast:transcript url="https://upstream.example.com/ep1.vtt" type="text/vtt"/>
    </item>
  </channel>
</rss>"""
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.vtt'

    def test_duplicate_guid_does_not_cross_contaminate(self):
        """A keyed lookup can't tell apart two items sharing a guid; positional
        matching must, so the second item must not inherit the first's URL."""
        feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Transcript Show</title>
    <item>
      <title>Ep One</title>
      <guid>dupe-guid</guid>
      <enclosure url="https://example.com/one.mp3" type="audio/mpeg"/>
      <podcast:transcript url="https://upstream.example.com/ep1.vtt" type="text/vtt"/>
    </item>
    <item>
      <title>Ep Two</title>
      <guid>dupe-guid</guid>
      <enclosure url="https://example.com/two.mp3" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""
        episodes = RSSParser().extract_episodes(feed)
        by_title = {ep['title']: ep for ep in episodes}
        assert by_title['Ep One']['upstream_transcript_url'] == \
            'https://upstream.example.com/ep1.vtt'
        assert by_title['Ep Two']['upstream_transcript_url'] is None


ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <title>Atom Show</title><id>urn:atom-show</id><updated>2026-01-01T00:00:00Z</updated>
  <entry>
    <title>Ep One</title><id>ep-one</id><updated>2026-01-01T00:00:00Z</updated>
    <link rel="enclosure" href="https://example.com/one.mp3" type="audio/mpeg"/>
    <podcast:transcript url="https://upstream.example.com/ep1.vtt" type="text/vtt"/>
  </entry>
</feed>"""


class TestPositionalAlignment:
    def test_coincidental_count_match_with_different_order_falls_back_to_keyed(self):
        """Finding 4: a reordered entries list that happens to have the same
        length as the raw items must not be trusted by index; the guid/
        enclosure mismatch must trigger the keyed fallback instead."""
        feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Transcript Show</title>
    <item>
      <title>Ep A</title>
      <guid>guid-a</guid>
      <enclosure url="https://example.com/a.mp3" type="audio/mpeg"/>
      <podcast:transcript url="https://upstream.example.com/epA.vtt" type="text/vtt"/>
    </item>
    <item>
      <title>Ep B</title>
      <guid>guid-b</guid>
      <enclosure url="https://example.com/b.mp3" type="audio/mpeg"/>
      <podcast:transcript url="https://upstream.example.com/epB.vtt" type="text/vtt"/>
    </item>
  </channel>
</rss>"""
        parser = RSSParser()
        real_feed = parser.parse_feed(feed)
        assert [e.get('id') for e in real_feed.entries] == ['guid-a', 'guid-b']

        class _ReorderedFeed:
            entries = [real_feed.entries[1], real_feed.entries[0]]

        episodes = parser.extract_episodes(feed, parsed_feed=_ReorderedFeed())
        by_url = {e['url']: e for e in episodes}
        assert by_url['https://example.com/a.mp3']['upstream_transcript_url'] == \
            'https://upstream.example.com/epA.vtt'
        assert by_url['https://example.com/b.mp3']['upstream_transcript_url'] == \
            'https://upstream.example.com/epB.vtt'


class TestFeedWithoutChannel:
    def test_atom_feed_parses_episodes_without_transcript_fields(self):
        parser = RSSParser()
        assert parser.find_channel_element(ATOM_FEED) is None
        assert parser._parse_upstream_transcript_tags(None) == ([], {})
        episodes = parser.extract_episodes(ATOM_FEED)
        assert [e['url'] for e in episodes] == ['https://example.com/one.mp3']
        assert episodes[0]['upstream_transcript_url'] is None
        assert episodes[0]['upstream_transcript_type'] is None
