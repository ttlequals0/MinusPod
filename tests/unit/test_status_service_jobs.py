"""StatusService tracks several jobs keyed by episode."""
import pytest


@pytest.fixture
def ss(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    import status_service
    # Large soft timeout: legacy fixture data below uses a fixed started_at
    # that must survive _expire_stale, not race the real default.
    monkeypatch.setattr(status_service, '_get_soft_timeout', lambda: 10**15)
    status_service.StatusService._instance = None
    s = status_service.StatusService()
    yield s
    status_service.StatusService._instance = None


def test_two_jobs_update_independently(ss):
    ss.start_job('a', '1', 'A', 'Pod A')
    ss.start_job('b', '2', 'B', 'Pod B')
    ss.update_job_stage('b', '2', 'transcribing', 40)
    status = ss.get_status()
    assert [(j.slug, j.stage, j.progress) for j in status.jobs] == [
        ('a', 'downloading', 0.0), ('b', 'transcribing', 40)]
    assert status.current_job.slug == 'a'


def test_complete_one_leaves_the_other(ss):
    ss.start_job('a', '1', 'A', 'Pod A')
    ss.start_job('b', '2', 'B', 'Pod B')
    ss.complete_job('a', '1')
    status = ss.get_status()
    assert [j.slug for j in status.jobs] == ['b']
    assert status.current_job.slug == 'b'
    ss.fail_job('b', '2')
    assert ss.get_status().jobs == []
    assert ss.get_status().current_job is None


def test_to_dict_has_jobs_and_current_job(ss):
    ss.start_job('a', '1', 'A', 'Pod A')
    d = ss.to_dict()
    assert d['currentJob']['episodeId'] == '1'
    assert [j['episodeId'] for j in d['jobs']] == ['1']
    assert set(d['jobs'][0]) == set(d['currentJob'])


def test_clear_if_matches_only_that_job(ss):
    ss.start_job('a', '1', 'A', 'Pod A')
    ss.start_job('b', '2', 'B', 'Pod B')
    assert ss.clear_if_matches('a', '1') is True
    assert ss.clear_if_matches('a', '1') is False
    assert [j.slug for j in ss.get_status().jobs] == ['b']


def test_legacy_current_job_file_is_read(ss):
    ss._write_status_file({'current_job': {
        'slug': 'a', 'episode_id': '1', 'title': 'A', 'podcast_name': 'P',
        'started_at': 1.0, 'stage': 'detecting', 'progress': 50},
        'queued_episodes': [], 'feed_refreshes': {}, 'last_updated': 1.0})
    status = ss.get_status()
    assert status.current_job.stage == 'detecting'
