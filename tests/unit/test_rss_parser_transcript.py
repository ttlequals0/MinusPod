"""Upstream podcast:transcript capture (2.98.0 transcript differential).

feedparser flattens multiple podcast:transcript tags on one item down to a
single dict, so picking the best of several requires a raw-XML pass over
the item (matched by guid or enclosure URL), mirroring the upstream
podcast:chapters precedent in test_upstream_chapters.py.
"""
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
