"""modify_feed with prefetched podcasting2_flags matches the storage-lookup path."""
from rss_parser import RSSParser

FEED = '''<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>Example Feed</title><link>https://example.com</link>
    <description>Example</description>
    <item>
      <title>Example Episode</title><guid>episode-guid</guid>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg" />
    </item>
  </channel>
</rss>'''
DB_EPISODE = 'a1b2c3d4e5f6'


class CountingStorage:
    def __init__(self, vtt=True, chapters=True):
        self.vtt, self.chapters, self.calls = vtt, chapters, 0

    def has_artwork(self, slug):
        return False

    def has_transcript_vtt(self, slug, episode_id):
        self.calls += 1
        return self.vtt

    def has_chapters_json(self, slug, episode_id):
        self.calls += 1
        return self.chapters


def _ids():
    return RSSParser(base_url='https://minuspod.example').generate_episode_id(
        'https://example.com/audio.mp3', 'episode-guid')


def _render(storage, flags=None, feed_auth_key=None):
    extra = [{'episode_id': DB_EPISODE, 'new_duration': 100, 'title': 'Extra'}]
    return RSSParser(base_url='https://minuspod.example').modify_feed(
        FEED, 'example-feed', storage=storage, extra_episodes=extra, podcasting2_flags=flags,
        feed_auth_key=feed_auth_key)


def _p2_lines(xml):
    return [line.strip() for line in xml.splitlines()
            if '<podcast:transcript' in line or '<podcast:chapters' in line]


def test_flags_match_storage_lookup_and_skip_storage():
    reference = _p2_lines(_render(CountingStorage()))
    assert len(reference) == 4
    storage = CountingStorage()
    flags = {_ids(): (True, True), DB_EPISODE: (True, True)}
    assert _p2_lines(_render(storage, flags)) == reference
    assert storage.calls == 0


def test_partial_flags_emit_only_the_flagged_tags():
    storage = CountingStorage()
    xml = _render(storage, {_ids(): (True, False), DB_EPISODE: (False, True)})
    lines = _p2_lines(xml)
    assert len(lines) == 2
    assert '<podcast:transcript' in lines[0] and '<podcast:chapters' in lines[1]
    assert storage.calls == 0


def test_episode_missing_from_flags_emits_nothing():
    storage = CountingStorage()
    assert _p2_lines(_render(storage, {})) == []
    assert storage.calls == 0


def test_appended_item_tag_urls_carry_the_feed_key():
    flags = {_ids(): (True, True), DB_EPISODE: (True, True)}
    lines = _p2_lines(_render(CountingStorage(), flags, feed_auth_key='testkey'))
    assert len(lines) == 4
    assert all('?key=testkey' in line for line in lines)
