"""Status hold block reports SponsorBlock only when it is unreachable or episodes wait on it."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('status_sb_hold_test_')

from api import status  # noqa: E402
import database  # noqa: E402
import pytest  # noqa: E402
from offline_queue import record_probe_state  # noqa: E402


@pytest.fixture
def db():
    db = database.Database()
    db.create_podcast('yt-sb-hold', 'youtube://playlist/PL1', title='YT', feed_type='youtube')
    db.upsert_episode('yt-sb-hold', 'a1b2c3d4e5f6', title='Ep', status='pending',
                      original_url='https://example.com/v')
    yield db
    db.delete_podcast('yt-sb-hold')


def _view(db, enabled):
    with patch.object(status.yt_settings, 'enabled', return_value=enabled):
        return [s for s in status._build_hold_block(db)['offlineServices'] if s['service'] == 'sponsorblock']


def test_unreachable_with_youtube_enabled_is_listed(db):
    db.update_episode_sponsorblock('yt-sb-hold', 'a1b2c3d4e5f6', sb_state='waiting')
    record_probe_state(db, 'sponsorblock', False)
    entry = _view(db, True)[0]
    assert entry['held'] == 1 and entry['reachable'] is False and entry['checkedAt']
    assert status._build_hold_block(db)['offlineHeld'] == 0


def test_omitted_when_youtube_disabled_or_idle_and_reachable(db):
    db.update_episode_sponsorblock('yt-sb-hold', 'a1b2c3d4e5f6', sb_state='done')
    record_probe_state(db, 'sponsorblock', False)
    assert _view(db, False) == []
    record_probe_state(db, 'sponsorblock', True)
    assert _view(db, True) == []


def test_reachable_but_waiting_episodes_are_listed(db):
    db.update_episode_sponsorblock('yt-sb-hold', 'a1b2c3d4e5f6', sb_state='probing')
    record_probe_state(db, 'sponsorblock', True)
    assert _view(db, True)[0]['reachable'] is True


def test_deferred_services_stay_hidden_when_down_and_idle(db):
    for service in ('llm', 'whisper'):
        record_probe_state(db, service, False)
    with patch.object(status.yt_settings, 'enabled', return_value=True):
        services = [s['service'] for s in status._build_hold_block(db)['offlineServices']]
    assert services == []


def test_sponsorblock_down_and_idle_is_listed_with_zero_held(db):
    record_probe_state(db, 'sponsorblock', False)
    entry = _view(db, True)[0]
    assert entry['held'] == 0 and entry['reachable'] is False
