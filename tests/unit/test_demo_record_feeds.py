import pytest

from scripts.demo.record_feeds import Abort, feed_key, keep_snapshot_items, record_feed, strip_feed_keys

HOSTS = ['pods.example.net']
KEY = 'a' * 64
FEED = (
    '<?xml version="1.0"?>\n<!-- minuspod-rss-render-version:4 -->\n<rss><channel>\n'
    f'<image><url>https://pods.example.net/show/cover-minuspod-0123abcd-{KEY}.jpg</url></image>\n'
    '<managingEditor>host@pods.example.net</managingEditor>\n'
    f'<item><title>Kept</title><enclosure url="https://pods.example.net/episodes/show/4b6634e0dd38.mp3?key={KEY}"/></item>\n'
    f'<item><title>Gone</title><enclosure url="https://pods.example.net/episodes/show/ffffffffffff.mp3?key={KEY}&amp;v=2"/></item>\n'
    '<item><title>No audio</title></item>\n'
    '</channel></rss>\n'
)


def test_strip_feed_keys_removes_query_keys_and_cover_tokens():
    out = strip_feed_keys(FEED)
    assert 'key=' not in out
    assert '/show/cover-minuspod-0123abcd.jpg' in out
    assert 'ffffffffffff.mp3?v=2' in out


def test_keep_snapshot_items_drops_unknown_episodes_and_keeps_items_without_audio():
    out, dropped = keep_snapshot_items(FEED, {('show', '4b6634e0dd38')})
    assert dropped == 1
    assert 'Kept' in out and 'No audio' in out and 'Gone' not in out


def test_record_feed_rewrites_host_and_emails():
    out, dropped = record_feed(FEED, HOSTS, {('show', '4b6634e0dd38')})
    assert dropped == 1
    assert 'pods.example.net' not in out
    assert '<enclosure url="https://demo.minuspod.app/episodes/show/4b6634e0dd38.mp3"/>' in out
    assert 'contact@example.com' in out
    assert '<!-- minuspod-rss-render-version:4 -->' in out


def test_record_feed_refuses_a_feed_without_render_marker():
    with pytest.raises(Abort):
        record_feed(FEED.replace('<!-- minuspod-rss-render-version:4 -->\n', ''), HOSTS, set())


def test_feed_key_reads_the_key_from_a_feed_url():
    assert feed_key([{'feedUrl': 'https://pods.example.net/show'}, {'feedUrl': f'https://pods.example.net/other?key={KEY}'}]) == KEY
    assert feed_key([{'feedUrl': 'https://pods.example.net/show'}]) is None


def test_main_writes_no_file_when_any_feed_has_a_leak_hit(tmp_path, monkeypatch):
    import sqlite3
    from unittest import mock
    from scripts.demo import record_feeds
    db = tmp_path / 's.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE podcasts (id INTEGER, slug TEXT)')
    con.execute('CREATE TABLE episodes (podcast_id INTEGER, episode_id TEXT)')
    con.executemany('INSERT INTO podcasts VALUES (?, ?)', [(1, 'a-show'), (2, 'b-show')])
    con.commit()
    con.close()
    (tmp_path / 'ex.json').write_text('{"slugs": []}')
    (tmp_path / 'c.txt').write_text('# Netscape HTTP Cookie File\n')
    clean = '<rss><!-- minuspod-rss-render-version:4 --></rss>'
    dirty = clean + ' 8.8.8.8'

    class Resp:
        def __init__(self, text, status=200, data=None):
            self.text, self.status_code, self._data = text, status, data

        def json(self):
            return self._data

    def get(url, **kw):
        if url.endswith('/feeds?includeLatestEpisodes=false'):
            return Resp('', data={'feeds': []})
        return Resp(dirty if url.endswith('/b-show') else clean)

    session = mock.Mock()
    session.get.side_effect = get
    monkeypatch.setattr(record_feeds.requests, 'Session', lambda: session)
    monkeypatch.setattr(record_feeds, 'MozillaCookieJar', lambda p: mock.Mock())
    out = tmp_path / 'out'
    rc = record_feeds.main([
        '--base-url', 'https://pods.example.net', '--cookies', str(tmp_path / 'c.txt'), '--sqlite', str(db),
        '--out', str(out), '--hosts', 'pods.example.net', '--exclusions', str(tmp_path / 'ex.json')])
    assert rc == 2
    assert not out.exists()
