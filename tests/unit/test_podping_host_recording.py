"""Listener records the host of every podping IRI, not only matching feeds."""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_host_recording_test_')

from podping_listener import PodpingListener, feed_url_domain


class FakeDb:
    def __init__(self):
        self.recorded = []

    def get_all_podcasts(self):
        return [{'slug': 'mine', 'source_url': 'https://feeds.megaphone.fm/mine'}]

    def set_last_podping_at(self, slug):
        pass

    def record_podping_hosts(self, counts):
        self.recorded.append(dict(counts))


@pytest.mark.parametrize('url,expected', [
    ('https://feeds.megaphone.fm/ABC123', 'feeds.megaphone.fm'),
    ('https://FEEDS.Megaphone.FM/ABC', 'feeds.megaphone.fm'),
    ('https://example.com:8443/feed.xml', 'example.com'),
    ('https://user:pw@example.com/feed.xml', 'example.com'),
    ('not a url', ''),
    ('', ''),
])
def test_feed_url_domain(url, expected):
    assert feed_url_domain(url) == expected


def _listener(db):
    listener = PodpingListener(db=db, rpc=lambda *a, **k: None, sleep=lambda s: None)
    listener.allowed_accounts = {'podping.bot'}
    listener._refresh_feed_map()
    return listener


def test_buffer_counts_every_iri_domain_including_unmatched():
    listener = _listener(FakeDb())
    listener._buffer_hosts([
        'https://feeds.megaphone.fm/mine',
        'https://anchor.fm/someone-else',
        'https://anchor.fm/another',
    ])
    assert listener.host_buffer == {'feeds.megaphone.fm': 1, 'anchor.fm': 2}


def test_buffer_skips_unparseable_iris():
    listener = _listener(FakeDb())
    listener._buffer_hosts(['', 'not a url', None, 123])
    assert listener.host_buffer == {}


def test_flush_writes_and_clears_the_buffer():
    db = FakeDb()
    listener = _listener(db)
    listener._buffer_hosts(['https://anchor.fm/x'])
    listener._flush_host_buffer()
    assert db.recorded == [{'anchor.fm': 1}]
    assert listener.host_buffer == {}


def test_flush_is_a_noop_when_the_buffer_is_empty():
    db = FakeDb()
    listener = _listener(db)
    listener._flush_host_buffer()
    assert db.recorded == []


class FailingDb(FakeDb):
    def record_podping_hosts(self, counts):
        raise RuntimeError('db down')


def test_flush_failure_does_not_lose_the_buffer():
    listener = _listener(FailingDb())
    listener._buffer_hosts(['https://anchor.fm/x'])
    listener._flush_host_buffer()
    assert listener.host_buffer == {'anchor.fm': 1}


def test_flush_failure_still_backs_off():
    # Without this the listener would retry, and log a traceback, every tick.
    listener = _listener(FailingDb())
    listener._buffer_hosts(['https://anchor.fm/x'])
    listener._flush_host_buffer()
    assert listener.host_flushed_at > 0.0
