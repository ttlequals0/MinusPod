"""Enclosure length attribute (RSS spec, validator warning fix).

Processed items get the stored processed file size; unprocessed items
(served through our proxy URL but not yet cut) pass through the upstream
length when present and omit the attribute otherwise. Never a guessed or
zero value.
"""
from rss_parser import RSSParser


FEED_TEMPLATE = '''<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel><title>Example Feed</title><link>https://example.com</link>
    <description>Example</description>
    <item>
      <title>Example Episode</title><guid>episode-guid</guid>
      <enclosure url="https://example.com/audio.mp3" type="audio/mpeg"{length} />
    </item>
  </channel>
</rss>'''


def _render(length='', extra_episodes=None):
    parser = RSSParser(base_url='https://minuspod.example')
    return parser.modify_feed(
        FEED_TEMPLATE.format(length=length),
        'example-feed',
        extra_episodes=extra_episodes,
    )


def _episode_id():
    parser = RSSParser(base_url='https://minuspod.example')
    return parser.generate_episode_id('https://example.com/audio.mp3', 'episode-guid')


def _enclosure_line(xml):
    return next(line for line in xml.splitlines() if '<enclosure ' in line)


def test_processed_episode_uses_stored_size():
    extra = [{'episode_id': _episode_id(), 'new_duration': 100, 'processed_size_bytes': 1600000}]
    xml = _render(' length="99"', extra)

    assert 'length="1600000"' in _enclosure_line(xml)


def test_unprocessed_episode_keeps_upstream_length():
    xml = _render(' length="42000"')

    assert 'length="42000"' in _enclosure_line(xml)


def test_unprocessed_episode_omits_length_when_upstream_missing():
    xml = _render('')

    assert 'length="' not in _enclosure_line(xml)


def test_unprocessed_episode_omits_length_when_upstream_is_zero():
    xml = _render(' length="0"')

    assert 'length="' not in _enclosure_line(xml)


def test_unprocessed_episode_omits_length_when_upstream_is_not_numeric():
    xml = _render(' length="unknown"')

    assert 'length="' not in _enclosure_line(xml)


def test_appended_db_episode_beyond_cap_uses_stored_size():
    # Not one of the upstream entries, so it renders via _append_db_episode_item.
    extra = [{'episode_id': 'archived-episode', 'title': 'Old one',
              'processed_size_bytes': 2500000}]
    xml = _render('', extra)

    item = next(part for part in xml.split('<item>')[1:]
                if 'archived-episode' in part)
    assert 'length="2500000"' in item


def test_appended_db_episode_omits_length_when_size_unknown():
    extra = [{'episode_id': 'archived-episode', 'title': 'Old one'}]
    xml = _render('', extra)

    item = next(part for part in xml.split('<item>')[1:]
                if 'archived-episode' in part)
    assert 'length="' not in item


class _FakeStorage:
    def __init__(self, path):
        self._path = path

    def get_episode_path(self, slug, episode_id, extension='.mp3', version=None):
        return self._path


class _FakeDb:
    def __init__(self):
        self.calls = []

    def upsert_episode(self, slug, episode_id, **kwargs):
        self.calls.append((slug, episode_id, kwargs))


def test_backfill_processed_size_stats_and_persists_once(tmp_path):
    audio = tmp_path / 'episode.mp3'
    audio.write_bytes(b'\x00' * 12345)
    parser = RSSParser(base_url='https://minuspod.example')
    db = _FakeDb()
    ep = {'episode_id': 'ep1', 'processed_version': 2, 'processed_size_bytes': None}

    size = parser.backfill_processed_size(db, _FakeStorage(audio), 'slug', ep)

    assert size == 12345
    assert ep['processed_size_bytes'] == 12345
    assert db.calls == [('slug', 'ep1', {'processed_size_bytes': 12345})]


def test_backfill_processed_size_skips_stat_and_db_write_when_already_stored():
    parser = RSSParser(base_url='https://minuspod.example')
    db = _FakeDb()
    ep = {'episode_id': 'ep1', 'processed_size_bytes': 999}

    size = parser.backfill_processed_size(db, _FakeStorage(None), 'slug', ep)

    assert size == 999
    assert db.calls == []


def test_backfill_processed_size_returns_none_when_file_missing(tmp_path):
    missing = tmp_path / 'nope.mp3'
    parser = RSSParser(base_url='https://minuspod.example')
    db = _FakeDb()
    ep = {'episode_id': 'ep1', 'processed_size_bytes': None}

    size = parser.backfill_processed_size(db, _FakeStorage(missing), 'slug', ep)

    assert size is None
    assert ep['processed_size_bytes'] is None
    assert db.calls == []
