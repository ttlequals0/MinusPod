"""Require manual approval: resolver, finalize stamps, serving, approve routes, event."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('manual_approval_test_', reset_storage=True)

from main_app import app, db  # noqa: E402
import config_transfer  # noqa: E402
import email_service  # noqa: E402
import webhook_service  # noqa: E402
from api import limiter  # noqa: E402
from local_feed_builder import _fetch_local_feed_episodes  # noqa: E402
from main_app.feeds import _without_episodes  # noqa: E402
from main_app.pipeline import finalize  # noqa: E402
from rss_parser import RSSParser  # noqa: E402

SLUG, OTHER = 'approval-feed', 'approval-other'
HELD, OPEN, EP3 = 'a1b2c3d4e5f6', 'b2c3d4e5f6a1', 'c3d4e5f6a1b2'


@pytest.fixture(autouse=True)
def _rows():
    limiter.reset()
    db.set_setting('require_manual_approval_default', 'false', is_default=False)
    for slug in (SLUG, OTHER):
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(SLUG, HELD, original_url='https://example.com/h.mp3', title='Held',
                      status='processed', processed_file='episodes/h.mp3',
                      published_at='2026-10-01T00:00:00Z', ads_removed=2,
                      approval_pending_at='2026-10-02T00:00:00Z')
    db.upsert_episode(SLUG, OPEN, original_url='https://example.com/o.mp3', title='Open',
                      status='processed', processed_file='episodes/o.mp3',
                      published_at='2026-10-03T00:00:00Z')
    db.upsert_episode(OTHER, EP3, original_url='https://example.com/x.mp3', title='Other',
                      status='processed', processed_file='episodes/x.mp3',
                      published_at='2026-10-04T00:00:00Z',
                      approval_pending_at='2026-10-05T00:00:00Z')
    # The insert path ignores the approval columns; a second upsert stamps them.
    db.upsert_episode(SLUG, HELD, approval_pending_at='2026-10-02T00:00:00Z')
    db.upsert_episode(OTHER, EP3, approval_pending_at='2026-10-05T00:00:00Z')
    yield
    for slug in (SLUG, OTHER):
        db.delete_podcast(slug)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def no_rebuild():
    with patch('main_app.feeds.refresh_rss_feed') as refresh, \
            patch('recents_feed.rebuild_recents_feed'), \
            patch('main_app.feeds.get_feed_map', return_value={
                SLUG: {'in': 'https://example.com/a.xml'},
                OTHER: {'in': 'https://example.com/b.xml'}}), \
            patch('api.episodes.approval.fire_event') as fired:
        yield refresh, fired


class TestResolver:
    def test_inherits_global_default(self):
        assert db.requires_manual_approval(SLUG) is False
        db.set_setting('require_manual_approval_default', 'true', is_default=False)
        assert db.requires_manual_approval(SLUG) is True

    def test_feed_override_wins_both_ways(self):
        db.set_setting('require_manual_approval_default', 'true', is_default=False)
        db.update_podcast(SLUG, require_manual_approval=0)
        assert db.requires_manual_approval(SLUG) is False
        db.set_setting('require_manual_approval_default', 'false', is_default=False)
        db.update_podcast(SLUG, require_manual_approval=1)
        assert db.requires_manual_approval(SLUG) is True

    def test_podcast_dict_skips_lookup(self):
        assert db.requires_manual_approval(SLUG, podcast={'require_manual_approval': 1}) is True


def _persist(slug, episode_id, hold, podcast=None):
    """Persist through the real stamp and report whether the row is left waiting."""
    finalize._persist_episode_state(
        slug, episode_id, 1, 0, 1, 100.0, 90.0, 1,
        approval=finalize._approval_stamp(slug, episode_id, hold, podcast))
    return db.get_episode(slug, episode_id)['approval_pending_at'] is not None


class TestFinalizeStamp:
    def test_normal_run_waits_when_required(self):
        db.update_podcast(OTHER, require_manual_approval=1)
        db.upsert_episode(OTHER, EP3, approval_pending_at=None)
        assert _persist(OTHER, EP3, True, db.get_podcast_by_slug(OTHER)) is True

    def test_existing_wait_is_kept(self):
        db.update_podcast(SLUG, require_manual_approval=1)
        assert _persist(SLUG, HELD, True) is True
        assert db.get_episode(SLUG, HELD)['approval_pending_at'] == '2026-10-02T00:00:00Z'

    def test_approved_row_is_never_gated_again(self):
        db.update_podcast(SLUG, require_manual_approval=1)
        db.upsert_episode(SLUG, OPEN, approved_at='2026-10-09T00:00:00Z')
        assert _persist(SLUG, OPEN, True) is False
        assert db.get_episode(SLUG, OPEN)['approved_at'] == '2026-10-09T00:00:00Z'

    def test_fresh_row_waits_on_a_gated_feed(self):
        db.update_podcast(SLUG, require_manual_approval=1)
        assert _persist(SLUG, OPEN, True) is True

    def test_approve_during_reprocess_stays_approved(self):
        db.update_podcast(SLUG, require_manual_approval=1)
        stamp = finalize._approval_stamp(SLUG, HELD, True)
        assert stamp['approval_pending_at'] == '2026-10-02T00:00:00Z'
        assert db.approve_episode(SLUG, HELD, '2026-10-09T00:00:00Z')
        finalize._persist_episode_state(
            SLUG, HELD, 1, 0, 1, 100.0, 90.0, 1,
            approval=finalize._approval_stamp(SLUG, HELD, True))
        row = db.get_episode(SLUG, HELD)
        assert row['approval_pending_at'] is None and row['approved_at']

    def test_not_required_clears_the_wait(self):
        assert _persist(SLUG, HELD, True) is False
        assert db.get_episode(SLUG, HELD)['approved_at'] is None

    def test_default_publishes_even_when_required(self):
        db.update_podcast(SLUG, require_manual_approval=1)
        assert _persist(SLUG, HELD, False) is False
        assert db.get_episode(SLUG, HELD)['approved_at']

    @pytest.mark.parametrize('hold,event', [
        (True, webhook_service.EVENT_EPISODE_AWAITING_APPROVAL),
        (False, webhook_service.EVENT_EPISODE_PROCESSED)])
    def test_finalize_episode_fires_the_matching_event(self, hold, event):
        db.update_podcast(SLUG, require_manual_approval=1)
        with patch.object(finalize.common, '_refresh_rss_for_slug'), \
                patch.object(finalize, 'fire_event') as fired:
            finalize._finalize_episode(SLUG, OPEN, 'Open', SLUG, 1, 0, 1, 100.0, 90.0,
                                       0.0, processed_version=1, hold_for_approval=hold)
        assert fired.call_args.kwargs['event'] == event
        assert (db.get_episode(SLUG, OPEN)['approval_pending_at'] is not None) is hold


class TestServedFeeds:
    def test_extra_episodes_and_ids(self):
        pid = db.get_podcast_by_slug(SLUG)['id']
        assert [e['episode_id'] for e in db.get_processed_episodes_for_feed(pid)] == [OPEN]
        assert db.get_awaiting_approval_episode_ids(pid) == [HELD]

    def test_served_rss_drops_awaiting_upstream_entry(self):
        xml = ('<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
               '<item><title>E</title><enclosure url="https://example.com/e.mp3" '
               'type="audio/mpeg"/><guid>g</guid></item></channel></rss>')
        parser = RSSParser(base_url='http://x')
        eid = parser.generate_episode_id('https://example.com/e.mp3', 'g')
        parsed = parser.parse_feed(xml, source='s')
        assert len(parsed.entries) == 1
        assert _without_episodes(parsed, set()) is parsed
        assert _without_episodes(parsed, {eid}).entries == []
        assert len(parsed.entries) == 1

    def test_recents_excludes_awaiting(self):
        ids = {r['episode_id'] for r in db.get_recent_processed_episodes('2026-01-01')}
        assert OPEN in ids and HELD not in ids and EP3 not in ids

    @pytest.mark.parametrize('processed_only', [True, False])
    def test_local_and_youtube_builders_exclude_awaiting(self, processed_only):
        pid = db.get_podcast_by_slug(SLUG)['id']
        rows = _fetch_local_feed_episodes(db, pid, None, processed_only=processed_only)
        assert [r['episode_id'] for r in rows] == [OPEN]


class TestAudioRoute:
    @pytest.mark.parametrize('method', ['get', 'head'])
    def test_awaiting_episode_answers_503(self, client, method):
        with patch('main_app.routes.get_feed_map', return_value={SLUG: {}}):
            resp = getattr(client, method)(f'/episodes/{SLUG}/{HELD}.mp3')
        assert resp.status_code == 503
        assert resp.headers['Retry-After'] == '1800'
        if method == 'get':
            assert resp.get_json() == {'error': 'awaiting_approval'}


class TestApproveRoutes:
    def test_single_approve(self, client, no_rebuild):
        refresh, fired = no_rebuild
        resp = client.post(f'/api/v1/feeds/{SLUG}/episodes/{HELD}/approve')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['approved'] is True
        assert body['episode']['awaitingApproval'] is False and body['episode']['approvedAt']
        assert db.get_episode(SLUG, HELD)['approval_pending_at'] is None
        refresh.assert_called_once()
        assert fired.call_args.kwargs['event'] == webhook_service.EVENT_EPISODE_PROCESSED

    def test_single_approve_conflicts_when_not_waiting(self, client, no_rebuild):
        assert client.post(f'/api/v1/feeds/{SLUG}/episodes/{OPEN}/approve').status_code == 409
        assert client.post(f'/api/v1/feeds/{SLUG}/episodes/ffffffffffff/approve').status_code == 409

    def test_list_awaiting_and_scope(self, client):
        body = client.get('/api/v1/episodes/awaiting-approval').get_json()
        assert body['count'] == 2
        assert body['episodes'][0] == {
            'slug': SLUG, 'episodeId': HELD, 'title': 'Held', 'podcast': SLUG,
            'pendingSince': '2026-10-02T00:00:00Z', 'adCount': 2, 'pendingReviewCount': 0}
        scoped = client.get(f'/api/v1/episodes/awaiting-approval?slug={OTHER}&limit=1').get_json()
        assert scoped['count'] == 1 and scoped['episodes'][0]['episodeId'] == EP3

    def test_bulk_by_list_counts_skipped_and_rebuilds_once_per_feed(self, client, no_rebuild):
        refresh, _ = no_rebuild
        resp = client.post('/api/v1/episodes/awaiting-approval/approve', json={'episodes': [
            {'slug': SLUG, 'episodeId': HELD}, {'slug': SLUG, 'episodeId': OPEN},
            {'slug': OTHER, 'episodeId': EP3}]})
        assert resp.get_json() == {'approved': 2, 'skipped': 1, 'remaining': 0}
        assert refresh.call_count == 2

    def test_bulk_by_slug(self, client, no_rebuild):
        resp = client.post('/api/v1/episodes/awaiting-approval/approve', json={'slug': SLUG})
        assert resp.get_json() == {'approved': 1, 'skipped': 0, 'remaining': 0}
        assert db.get_episode(OTHER, EP3)['approval_pending_at']

    def test_bulk_by_slug_caps_at_max_oldest_first(self, client, no_rebuild):
        from api.episodes import approval
        for i in range(approval.BULK_APPROVE_MAX + 3):
            db.upsert_episode(OTHER, f'{i:012x}', original_url='https://example.com/m.mp3',
                              status='processed', processed_file='episodes/m.mp3')
            db.upsert_episode(OTHER, f'{i:012x}',
                              approval_pending_at=f'2026-09-01T00:{i // 60:02d}:{i % 60:02d}Z')
        resp = client.post('/api/v1/episodes/awaiting-approval/approve', json={'slug': OTHER})
        assert resp.get_json() == {'approved': approval.BULK_APPROVE_MAX, 'skipped': 0, 'remaining': 4}
        assert db.get_episode(OTHER, EP3)['approval_pending_at']
        assert db.get_episode(OTHER, f'{0:012x}')['approval_pending_at'] is None

    def test_bulk_validates_body(self, client, no_rebuild):
        url = '/api/v1/episodes/awaiting-approval/approve'
        assert client.post(url, json={}).status_code == 400
        assert client.post(url, json={'episodes': [{'slug': SLUG}]}).status_code == 400
        assert client.post(url, json={'slug': 'nope-feed'}).status_code == 404

    def test_held_list(self, client):
        db.upsert_episode(SLUG, OPEN)
        db.get_connection().execute(
            "UPDATE episodes SET pending_review_count = 3 WHERE episode_id = ?", (OPEN,))
        db.get_connection().commit()
        body = client.get('/api/v1/episodes/held').get_json()
        assert body['count'] == 1
        assert body['episodes'][0]['episodeId'] == OPEN
        assert body['episodes'][0]['pendingReviewCount'] == 3


class TestApiFields:
    def test_feed_and_episode_payloads(self, client):
        db.update_podcast(SLUG, require_manual_approval=1)
        feed = client.get(f'/api/v1/feeds/{SLUG}').get_json()
        assert feed['requireManualApproval'] is True
        assert feed['awaitingApprovalCount'] == 1
        eps = client.get(f'/api/v1/feeds/{SLUG}/episodes').get_json()['episodes']
        held = next(e for e in eps if e['id'] == HELD)
        assert held['awaitingApproval'] is True
        assert held['approvalPendingAt'] == '2026-10-02T00:00:00Z'

    def test_patch_feed_override(self, client):
        assert client.patch(f'/api/v1/feeds/{SLUG}',
                            json={'requireManualApproval': True}).status_code == 200
        assert db.get_podcast_by_slug(SLUG)['require_manual_approval'] == 1
        client.patch(f'/api/v1/feeds/{SLUG}', json={'requireManualApproval': None})
        assert db.get_podcast_by_slug(SLUG)['require_manual_approval'] is None

    def test_global_setting_round_trip(self, client):
        assert client.put('/api/v1/settings/ad-detection',
                          json={'requireManualApprovalDefault': True}).status_code == 200
        assert db.get_setting('require_manual_approval_default') == 'true'
        got = client.get('/api/v1/settings').get_json()
        assert got['requireManualApprovalDefault']['value'] is True


class TestEvent:
    def test_registered_everywhere(self):
        event = 'Episode Awaiting Approval'
        assert webhook_service.EVENT_EPISODE_AWAITING_APPROVAL == event
        assert event in webhook_service.VALID_EVENTS
        assert event in email_service.FORMATTERS

    def test_email_and_test_context(self):
        ctx = webhook_service._build_test_context('Episode Awaiting Approval')
        assert ctx['event'] == 'Episode Awaiting Approval'
        subject, rows, note = email_service.FORMATTERS['Episode Awaiting Approval'](ctx)
        assert subject.startswith('[MinusPod] Episode Awaiting Approval:')
        assert 'approve' in note

    def test_notification_validation_accepts_it(self, client):
        resp = client.post('/api/v1/settings/webhooks', json={
            'url': 'https://example.com/hook', 'events': ['Episode Awaiting Approval']})
        assert resp.status_code in (200, 201)


def test_feed_field_survives_config_transfer_round_trip(temp_db, monkeypatch):
    temp_db.create_podcast('rt-feed', 'https://example.com/rt.xml', 'Round Trip')
    temp_db.update_podcast('rt-feed', require_manual_approval=1)
    exported = config_transfer.export_config(temp_db, '2.98.2')
    feed = next(f for f in exported['feeds'] if f['slug'] == 'rt-feed')
    assert feed['settings']['require_manual_approval'] is True
    temp_db.update_podcast('rt-feed', require_manual_approval=None)
    preview = config_transfer.build_preview(temp_db, exported, 'everything')
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_a: [])
    config_transfer.apply_config(temp_db, exported, 'everything', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('rt-feed')['require_manual_approval'] == 1


def test_migration_columns_and_index_exist():
    conn = db.get_connection()
    cols = {r['name'] for r in conn.execute('PRAGMA table_info(episodes)')}
    assert {'approval_pending_at', 'approved_at'} <= cols
    assert 'require_manual_approval' in {r['name'] for r in conn.execute('PRAGMA table_info(podcasts)')}
    idx = {r['name'] for r in conn.execute("PRAGMA index_list(episodes)")}
    assert 'idx_episodes_approval_pending' in idx
