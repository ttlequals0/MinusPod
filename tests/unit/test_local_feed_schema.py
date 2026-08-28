import pytest
from database import Database
from database.podcasts import is_local_feed


@pytest.fixture
def db(tmp_path):
    # Database is a singleton (see tests/conftest.py::temp_db) -- reset it
    # so each test gets a fresh database rather than reusing a prior one.
    Database._instance = None
    instance = Database(str(tmp_path))
    yield instance
    Database._instance = None


def test_feed_type_defaults_subscribed(db):
    db.create_podcast('sub-feed', 'https://example.com/feed.xml')
    row = db.get_podcast_by_slug('sub-feed')
    assert row['feed_type'] == 'subscribed'
    assert not is_local_feed(row)


def test_create_local_podcast(db):
    db.create_podcast('my-archive', 'local://my-archive',
                      title='My Archive', feed_type='local')
    row = db.get_podcast_by_slug('my-archive')
    assert row['feed_type'] == 'local'
    assert row['own_episode_guids'] == 1
    assert is_local_feed(row)


def test_p20_and_itunes_columns_roundtrip(db):
    db.create_podcast('my-archive', 'local://my-archive', feed_type='local')
    db.update_podcast('my-archive', author='Jane Host', explicit=1,
                      categories='["Technology"]',
                      p20_channel_json='{"medium": "podcast"}')
    row = db.get_podcast_by_slug('my-archive')
    assert row['author'] == 'Jane Host'
    assert row['explicit'] == 1
    assert row['categories'] == '["Technology"]'
    assert row['p20_channel_json'] == '{"medium": "podcast"}'


def test_feed_type_not_updatable(db):
    db.create_podcast('my-archive', 'local://my-archive', feed_type='local')
    db.update_podcast('my-archive', feed_type='subscribed')
    assert db.get_podcast_by_slug('my-archive')['feed_type'] == 'local'


def test_episode_local_columns(db):
    db.create_podcast('my-archive', 'local://my-archive', feed_type='local')
    db.upsert_episode('my-archive', 's01e05', original_url='local://s01e05',
                      title='Ep 5', status='discovered', season_number=1,
                      episode_number=5, p20_item_json='{"person": []}')
    ep = db.get_episode('my-archive', 's01e05')
    assert ep['season_number'] == 1
    assert ep['p20_item_json'] == '{"person": []}'
