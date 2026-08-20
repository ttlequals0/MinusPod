"""Per-run episode log API (#660): JSON view, raw download, 404 variants."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='run-log-api-test-'))

SLUG = 'run-log-api-feed'
EPISODE_ID = 'abc123def456'

LINES = [
    {'ts': '2026-08-20T00:00:00.000Z', 'level': 'DEBUG',
     'logger': 'podcast.audio', 'msg': 'window 1 prompt built'},
    {'ts': '2026-08-20T00:00:01.000Z', 'level': 'INFO',
     'logger': 'podcast.audio', 'msg': 'Starting'},
    {'ts': '2026-08-20T00:00:02.000Z', 'level': 'WARNING',
     'logger': 'podcast.claude', 'msg': 'window 2 retried'},
    {'ts': '2026-08-20T00:00:03.000Z', 'level': 'ERROR',
     'logger': 'podcast.audio', 'msg': 'cut failed'},
]


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


@pytest.fixture
def seeded(app_client):
    from api import get_database, get_storage
    import run_log

    db = get_database()
    storage = get_storage()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Run Log API Test')
    podcast = db.get_podcast_by_slug(SLUG)
    db.upsert_episode(SLUG, EPISODE_ID,
                      original_url='https://example.com/ep.mp3',
                      title='Run Log API Episode', status='processed')

    def add_run(with_log=True, prune=False, body=None):
        history_id = db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug=SLUG,
            podcast_title='Run Log API Test', episode_id=EPISODE_ID,
            episode_title='Run Log API Episode', status='completed')
        if not with_log:
            return history_id
        text = body if body is not None else ''.join(
            json.dumps(line) + '\n' for line in LINES)
        path = run_log.run_log_path(storage.data_dir, SLUG, EPISODE_ID, history_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        db.set_history_log_pointer(
            history_id,
            run_log.run_log_relative_path(SLUG, EPISODE_ID, history_id),
            path.stat().st_size)
        if prune:
            path.unlink()
        return history_id

    yield {'db': db, 'storage': storage, 'add_run': add_run}
    db.delete_podcast(SLUG)


def _get(client, run_number, query=''):
    return client.get(
        f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/runs/{run_number}/log{query}')


class TestJsonView:
    def test_returns_every_line(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        resp = _get(app_client, 1)

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['runNumber'] == 1
        assert body['lines'] == LINES
        assert body['truncated'] is False
        assert body['bytes'] > 0

    def test_level_filter_keeps_the_minimum_and_above(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        levels = [line['level'] for line in
                  _get(app_client, 1, '?level=warning').get_json()['lines']]

        assert levels == ['WARNING', 'ERROR']

    def test_an_unknown_level_is_rejected(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        resp = _get(app_client, 1, '?level=verbose')

        assert resp.status_code == 400
        assert 'level' in resp.get_json()['error']

    def test_a_truncation_marker_sets_the_flag(self, app_client, seeded):
        from run_log import TRUNCATION_MARKER
        body = ''.join(json.dumps(line) + '\n' for line in LINES)
        body += json.dumps({'ts': '2026-08-20T00:00:04.000Z', 'level': 'WARNING',
                            'logger': 'podcast.run_log',
                            'msg': TRUNCATION_MARKER}) + '\n'
        seeded['add_run'](body=body)
        _authed(app_client)

        assert _get(app_client, 1).get_json()['truncated'] is True

    def test_malformed_lines_are_skipped(self, app_client, seeded):
        body = json.dumps(LINES[0]) + '\nnot json at all\n'
        seeded['add_run'](body=body)
        _authed(app_client)

        assert _get(app_client, 1).get_json()['lines'] == [LINES[0]]


class TestLoudLevels:
    def test_critical_and_unknown_levels_survive_every_filter(self, app_client, seeded):
        body = ''.join(json.dumps(line) + '\n' for line in LINES)
        body += json.dumps({'ts': '2026-08-20T00:00:05.000Z', 'level': 'CRITICAL',
                            'logger': 'podcast.audio', 'msg': 'process is going down'}) + '\n'
        body += json.dumps({'ts': '2026-08-20T00:00:06.000Z', 'level': 'AUDIT',
                            'logger': 'podcast.audio', 'msg': 'unfamiliar level'}) + '\n'
        seeded['add_run'](body=body)
        _authed(app_client)

        for level in ('debug', 'info', 'warning', 'error', 'critical'):
            messages = [line['msg'] for line in
                        _get(app_client, 1, f'?level={level}').get_json()['lines']]
            assert 'process is going down' in messages, level
            assert 'unfamiliar level' in messages, level


class TestRawDownload:
    def test_raw_returns_the_file_verbatim(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        resp = _get(app_client, 1, '?format=raw')

        assert resp.status_code == 200
        assert resp.mimetype == 'text/plain'
        assert resp.data.decode() == ''.join(
            json.dumps(line) + '\n' for line in LINES)
        assert resp.headers['Content-Disposition'] == (
            f'attachment; filename="{SLUG}-{EPISODE_ID}-run1.jsonl"')

    def test_raw_denies_script_loading(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        resp = _get(app_client, 1, '?format=raw')

        assert resp.headers['Content-Security-Policy'] == "default-src 'none'"
        assert resp.headers['X-Content-Type-Options'] == 'nosniff'


class TestNotFound:
    def test_a_run_without_a_log_reports_not_stored(self, app_client, seeded):
        seeded['add_run'](with_log=False)
        _authed(app_client)

        resp = _get(app_client, 1)

        assert resp.status_code == 404
        assert resp.get_json()['code'] == 'log_not_stored'

    def test_a_deleted_file_reports_pruned(self, app_client, seeded):
        seeded['add_run'](prune=True)
        _authed(app_client)

        resp = _get(app_client, 1)

        assert resp.status_code == 404
        assert resp.get_json()['code'] == 'log_pruned'

    def test_a_pointer_outside_the_log_tree_serves_nothing(self, app_client, seeded):
        history_id = seeded['add_run']()
        seeded['db'].set_history_log_pointer(history_id, '../../podcast.db', 10)
        _authed(app_client)

        resp = _get(app_client, 1)

        assert resp.status_code == 404
        assert resp.get_json()['code'] == 'log_pruned'
        assert (seeded['storage'].data_dir / 'podcast.db').exists()

    def test_an_unknown_run_number_is_404(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        assert _get(app_client, 9).status_code == 404

    def test_an_unknown_episode_is_404(self, app_client, seeded):
        _authed(app_client)

        resp = app_client.get(
            f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/runs/1/log')

        assert resp.status_code == 404

    def test_a_malformed_episode_id_is_rejected(self, app_client, seeded):
        _authed(app_client)

        resp = app_client.get(
            f'/api/v1/feeds/{SLUG}/episodes/nosuchepisode/runs/1/log')

        assert resp.status_code == 400

    def test_a_traversal_slug_is_refused(self, app_client, seeded):
        seeded['add_run']()
        _authed(app_client)

        resp = app_client.get(
            f'/api/v1/feeds/..%2f..%2fetc/episodes/{EPISODE_ID}/runs/1/log')

        assert resp.status_code in (400, 404)

    def test_the_route_is_not_auth_exempt(self):
        from api import AUTH_EXEMPT_PATHS, PODCAST_APP_EXEMPT_PATTERNS

        path = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/runs/1/log'
        assert path not in AUTH_EXEMPT_PATHS
        assert not any(p.match(path) for p in PODCAST_APP_EXEMPT_PATTERNS)


class TestFeedDeletion:
    def test_deleting_the_feed_removes_its_logs(self, app_client, seeded):
        import run_log

        seeded['add_run']()
        log_dir = run_log.episode_log_root(seeded['storage'].data_dir) / SLUG
        assert log_dir.exists()
        _authed(app_client)
        csrf = None
        for cookie in app_client._cookies.values():
            if cookie.key == 'minuspod_csrf':
                csrf = cookie.value

        resp = app_client.delete(f'/api/v1/feeds/{SLUG}',
                                 headers={'X-CSRF-Token': csrf} if csrf else {})

        assert resp.status_code == 200
        assert not log_dir.exists()


class TestHasLogFlag:
    def test_episode_response_flags_runs_with_logs(self, app_client, seeded):
        seeded['add_run']()
        seeded['add_run'](with_log=False)
        _authed(app_client)

        runs = app_client.get(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()['processingRuns']

        assert [run['hasLog'] for run in runs] == [True, False]
