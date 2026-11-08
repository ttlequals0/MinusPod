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
