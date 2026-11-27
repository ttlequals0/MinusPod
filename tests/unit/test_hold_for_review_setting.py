"""hold_for_review setting: global default, per-feed column, API, upgrade migration, bulk close, config transfer."""
import json

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('hold_for_review_setting_test_', passphrase='hold-for-review-test-passphrase')

import database
from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    return database.Database()


@pytest.fixture
def feed_slug(db, preserve_setting):
    preserve_setting('hold_for_review')
    slug = 'hold-for-review-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Hold For Review')
    yield slug
    db.delete_podcast(slug)


def _held(start, end, **extra):
    return {'start': start, 'end': end, 'confidence': 0.9, 'detection_stage': 'claude',
            'held_for_review': True, 'hold_reason': 'uncorroborated_tail', 'was_cut': False,
            'validation': {'decision': 'REVIEW'}, **extra}


class TestResolver:
    def _resolve(self, db, slug):
        from config import resolve_hold_for_review
        return resolve_hold_for_review(db, slug)

    def test_default_is_off(self, db, feed_slug):
        db.clear_setting('hold_for_review')
        assert self._resolve(db, feed_slug) is False

    def test_null_inherits_global(self, db, feed_slug):
        db.set_setting('hold_for_review', 'true', is_default=False)
        assert self._resolve(db, feed_slug) is True

    def test_feed_off_beats_global_on(self, db, feed_slug):
        db.set_setting('hold_for_review', 'true', is_default=False)
        db.update_podcast(feed_slug, hold_for_review=0)
        assert self._resolve(db, feed_slug) is False

    def test_feed_on_beats_global_off(self, db, feed_slug):
        db.set_setting('hold_for_review', 'false', is_default=False)
        db.update_podcast(feed_slug, hold_for_review=1)
        assert self._resolve(db, feed_slug) is True

    def test_a_podcast_row_works_like_a_slug(self, db, feed_slug):
        from config import resolve_hold_for_review
        db.update_podcast(feed_slug, hold_for_review=1)
        assert resolve_hold_for_review(db, db.get_podcast_row(feed_slug)) is True


class TestApi:
    def test_global_put_get_round_trip(self, client, feed_slug):
        resp = client.put('/api/v1/settings/ad-detection', json={'holdForReview': True})
        assert resp.status_code == 200, resp.data
        assert resp.get_json()['holdForReview'] is True
        assert client.get('/api/v1/settings').get_json()['holdForReview']['value'] is True
        client.put('/api/v1/settings/ad-detection', json={'holdForReview': False})
        assert client.get('/api/v1/settings').get_json()['holdForReview']['value'] is False

    def test_default_flag_follows_null_reset(self, client, db, feed_slug):
        def state():
            return client.get('/api/v1/settings').get_json()['holdForReview']

        db.clear_setting('hold_for_review')
        assert state() == {'value': False, 'isDefault': True}
        client.put('/api/v1/settings/ad-detection', json={'holdForReview': True})
        assert state() == {'value': True, 'isDefault': False}
        client.put('/api/v1/settings/ad-detection', json={'holdForReview': None})
        assert state() == {'value': False, 'isDefault': True}

    def test_rejects_non_booleans_like_the_feed_patch(self, client, feed_slug):
        for bad in ('yes', 1, 0, 'false', [], {}):
            resp = client.put('/api/v1/settings/ad-detection', json={'holdForReview': bad})
            assert resp.status_code == 400, bad
            assert resp.get_json()['error'] == 'holdForReview must be true, false, or null'
            same = client.patch(f'/api/v1/feeds/{feed_slug}', json={'holdForReview': bad})
            assert same.get_json()['error'] == resp.get_json()['error']

    def test_put_without_the_field_does_not_echo_it(self, client, feed_slug):
        resp = client.put('/api/v1/settings/ad-detection', json={'whatsNewOnUpgrade': True})
        assert 'holdForReview' not in resp.get_json()

    def test_feed_patch_true_false_null(self, client, db, feed_slug):
        for sent, stored in ((True, 1), (False, 0), (None, None)):
            resp = client.patch(f'/api/v1/feeds/{feed_slug}', json={'holdForReview': sent})
            assert resp.status_code == 200, resp.data
            assert resp.get_json()['holdForReview'] is sent
            assert db.get_podcast_by_slug(feed_slug)['hold_for_review'] == stored
            assert client.get(f'/api/v1/feeds/{feed_slug}').get_json()['holdForReview'] is sent


class TestUpgradeMigration:
    MARKER = 'hold_for_review_default_upgrade'

    @staticmethod
    def _row(conn):
        row = conn.execute(
            "SELECT value, is_default FROM settings WHERE key = 'hold_for_review'").fetchone()
        return None if row is None else (row['value'], row['is_default'])

    @staticmethod
    def _rearm(conn):
        conn.execute("DELETE FROM schema_migrations WHERE name = ?", (TestUpgradeMigration.MARKER,))
        conn.commit()

    def test_a_fresh_database_keeps_the_default(self, temp_db):
        conn = temp_db.get_connection()
        assert temp_db._fresh_schema is True
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?",
                            (self.MARKER,)).fetchone()
        assert self._row(conn) in (None, ('0', 1))
        assert temp_db.get_setting_bool('hold_for_review') is False

    def test_an_existing_database_gets_an_explicit_true(self, temp_db):
        conn = temp_db.get_connection()
        self._rearm(conn)
        conn.execute("DELETE FROM settings WHERE key = 'hold_for_review'")
        conn.commit()
        temp_db._init_schema_inner()
        assert temp_db._fresh_schema is False
        assert self._row(conn) == ('true', 0)

    def test_a_seeded_default_row_is_overwritten(self, temp_db):
        conn = temp_db.get_connection()
        self._rearm(conn)
        temp_db.set_setting('hold_for_review', '0', is_default=True)
        temp_db._init_schema_inner()
        assert self._row(conn) == ('true', 0)

    def test_running_it_twice_changes_nothing(self, temp_db):
        conn = temp_db.get_connection()
        self._rearm(conn)
        temp_db._init_schema_inner()
        temp_db.set_setting('hold_for_review', 'false', is_default=False)
        temp_db._init_schema_inner()
        assert self._row(conn) == ('false', 0)

    def test_an_operator_choice_is_not_overwritten(self, temp_db):
        conn = temp_db.get_connection()
        temp_db.set_setting('hold_for_review', 'false', is_default=False)
        self._rearm(conn)
        temp_db._init_schema_inner()
        assert self._row(conn) == ('false', 0)


class TestBulkClose:
    def _episode(self, db, slug, episode_id, markers):
        db.upsert_episode(slug, episode_id, original_url='https://example.com/ep.mp3',
                          title=episode_id, status='processed')
        from config import count_pending_review
        db.save_episode_details(slug, episode_id, ad_markers=markers,
                                pending_review_count=count_pending_review(markers))

    def _markers(self, db, slug, episode_id):
        return json.loads(db.get_episode(slug, episode_id)['ad_markers_json'])

    @pytest.fixture
    def two_feeds(self, db, feed_slug):
        other = 'hold-for-review-other'
        db.create_podcast(other, 'https://example.com/other.xml', title='Other')
        self._episode(db, feed_slug, 'a1b2c3d4e5f6', [_held(10.0, 40.0), _held(100.0, 130.0, approved=True)])
        self._episode(db, other, 'b1c2d3e4f5a6', [_held(10.0, 40.0)])
        yield feed_slug, other
        db.delete_podcast(other)

    def test_all_feeds(self, client, db, two_feeds):
        feed, other = two_feeds
        resp = client.post('/api/v1/detections/close-pending', json={})
        assert resp.status_code == 200, resp.data
        assert resp.get_json() == {'closed': 2, 'episodes': 2, 'skipped': 0}
        a = self._markers(db, feed, 'a1b2c3d4e5f6')
        assert a[0]['closed_hold_reason'] == 'uncorroborated_tail' and a[0]['was_cut'] is False
        assert a[1]['held_for_review'] is True
        assert db.get_episode(feed, 'a1b2c3d4e5f6')['pending_review_count'] == 1
        assert db.get_episode(other, 'b1c2d3e4f5a6')['pending_review_count'] == 0

    def test_one_feed(self, client, db, two_feeds):
        feed, other = two_feeds
        resp = client.post('/api/v1/detections/close-pending', json={'feed': other})
        assert resp.get_json() == {'closed': 1, 'episodes': 1, 'skipped': 0}
        assert self._markers(db, feed, 'a1b2c3d4e5f6')[0]['held_for_review'] is True
        assert db.get_episode(feed, 'a1b2c3d4e5f6')['pending_review_count'] == 2

    def test_nothing_pending(self, client, db, feed_slug):
        resp = client.post('/api/v1/detections/close-pending', json={'feed': feed_slug})
        assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 0}

    def test_no_body_closes_everything(self, client, db, two_feeds):
        resp = client.post('/api/v1/detections/close-pending')
        assert resp.status_code == 200
        assert resp.get_json()['closed'] == 2

    def test_global_on_closes_nothing(self, client, db, two_feeds):
        feed, other = two_feeds
        db.set_setting('hold_for_review', 'true', is_default=False)
        resp = client.post('/api/v1/detections/close-pending', json={})
        assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 0}
        assert db.get_episode(feed, 'a1b2c3d4e5f6')['pending_review_count'] == 2

    def test_global_on_with_one_feed_overridden_off_closes_only_that_feed(self, client, db, two_feeds):
        feed, other = two_feeds
        db.set_setting('hold_for_review', 'true', is_default=False)
        db.update_podcast(other, hold_for_review=0)
        resp = client.post('/api/v1/detections/close-pending', json={})
        assert resp.get_json() == {'closed': 1, 'episodes': 1, 'skipped': 0}
        assert db.get_episode(other, 'b1c2d3e4f5a6')['pending_review_count'] == 0
        assert db.get_episode(feed, 'a1b2c3d4e5f6')['pending_review_count'] == 2

    def test_global_off_with_one_feed_overridden_on_leaves_that_feed(self, client, db, two_feeds):
        feed, other = two_feeds
        db.set_setting('hold_for_review', 'false', is_default=False)
        db.update_podcast(other, hold_for_review=1)
        resp = client.post('/api/v1/detections/close-pending', json={})
        assert resp.get_json() == {'closed': 1, 'episodes': 1, 'skipped': 0}
        assert db.get_episode(other, 'b1c2d3e4f5a6')['pending_review_count'] == 1

    def test_a_feed_filter_on_a_holding_feed_is_a_200_no_op(self, client, db, two_feeds):
        feed, _ = two_feeds
        db.set_setting('hold_for_review', 'true', is_default=False)
        resp = client.post('/api/v1/detections/close-pending', json={'feed': feed})
        assert resp.status_code == 200
        assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 0}

    def test_a_confirm_on_file_keeps_the_hold(self, client, db, two_feeds):
        feed, _ = two_feeds
        podcast_id = db.get_podcast_row(feed)['id']
        db.create_pattern_correction(
            correction_type='confirm', original_bounds={'start': 10.0, 'end': 40.0},
            podcast_id=podcast_id, episode_id='a1b2c3d4e5f6')
        resp = client.post('/api/v1/detections/close-pending', json={'feed': feed})
        assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 0}
        assert db.get_episode(feed, 'a1b2c3d4e5f6')['pending_review_count'] == 2

    def test_episodes_outside_the_processed_state_are_out_of_scope(self, client, db, two_feeds):
        feed, other = two_feeds
        for status in ('processing', 'failed'):
            db.upsert_episode(other, 'b1c2d3e4f5a6', status=status)
            resp = client.post('/api/v1/detections/close-pending', json={'feed': other})
            assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 0}
            assert self._markers(db, other, 'b1c2d3e4f5a6')[0]['held_for_review'] is True

    def test_a_write_between_read_and_close_is_kept_and_counted_skipped(self, client, db, two_feeds, monkeypatch):
        feed, other = two_feeds
        newer = [_held(10.0, 40.0, hold_reason='no_splice_evidence'), _held(300.0, 330.0)]
        original = database.Database.get_held_episodes

        def read_then_worker_writes(self, slug=None):
            rows = original(self, slug)
            self.save_episode_details(other, 'b1c2d3e4f5a6', ad_markers=newer, pending_review_count=2)
            return rows

        monkeypatch.setattr(database.Database, 'get_held_episodes', read_then_worker_writes)
        resp = client.post('/api/v1/detections/close-pending', json={'feed': other})
        assert resp.get_json() == {'closed': 0, 'episodes': 0, 'skipped': 1}
        assert self._markers(db, other, 'b1c2d3e4f5a6') == newer
        assert db.get_episode(other, 'b1c2d3e4f5a6')['pending_review_count'] == 2

    def test_no_correction_is_recorded_for_a_closed_hold(self, client, db, two_feeds):
        feed, _ = two_feeds
        podcast_id = db.get_podcast_row(feed)['id']
        client.post('/api/v1/detections/close-pending', json={'feed': feed})
        assert db.get_false_positive_corrections(podcast_id, 'a1b2c3d4e5f6') == []

    def test_detections_view_reports_the_closed_hold(self, client, db, two_feeds):
        feed, _ = two_feeds
        client.post('/api/v1/detections/close-pending', json={'feed': feed})
        items = client.get(f'/api/v1/detections?feed={feed}&status=rejected').get_json()['detections']
        closed = [i for i in items if i['autoClosed']]
        assert len(closed) == 1
        assert closed[0]['closedHoldReason'] == 'uncorroborated_tail'
        assert closed[0]['status'] == 'rejected' and closed[0]['resolution'] == 'unresolved'

    def test_episode_detail_reports_the_closed_hold(self, client, db, two_feeds):
        feed, _ = two_feeds
        client.post('/api/v1/detections/close-pending', json={'feed': feed})
        resp = client.get(f'/api/v1/feeds/{feed}/episodes/a1b2c3d4e5f6')
        assert resp.status_code == 200, resp.data
        body = resp.get_json()
        closed = [m for m in body['rejectedAdMarkers'] if m['autoClosed']]
        assert [m['closedHoldReason'] for m in closed] == ['uncorroborated_tail']
        assert all(m['autoClosed'] is False for m in body['pendingReviewMarkers'])

    def test_validation(self, client, feed_slug):
        assert client.post('/api/v1/detections/close-pending', json=[1]).status_code == 400
        assert client.post('/api/v1/detections/close-pending', json={'feed': 5}).status_code == 400
        assert client.post('/api/v1/detections/close-pending',
                           json={'feed': 'no-such-feed'}).status_code == 404


class TestConfigTransfer:
    def test_feed_column_is_exported_as_a_bool(self):
        import config_transfer
        assert 'hold_for_review' in config_transfer.FEED_COLUMNS
        assert 'hold_for_review' in config_transfer.FEED_BOOL_COLUMNS


class TestUpgradeMarkerFailsTowardHolding:
    MARKER = 'hold_for_review_default_upgrade'

    @staticmethod
    def _drop_marker(db):
        conn = db.get_connection()
        conn.execute("DELETE FROM schema_migrations WHERE name = ?", (TestUpgradeMarkerFailsTowardHolding.MARKER,))
        conn.commit()
        db._hold_upgrade_done = False

    def _resolve(self, db, slug_or_row):
        from config import resolve_hold_for_review
        return resolve_hold_for_review(db, slug_or_row)

    def test_marker_absent_resolves_on_even_with_the_default_off_row(self, db, feed_slug):
        db.set_setting('hold_for_review', '0', is_default=True)
        self._drop_marker(db)
        try:
            assert self._resolve(db, feed_slug) is True
        finally:
            db._run_hold_for_review_upgrade_migration(db.get_connection())

    def test_marker_present_resolves_the_stored_value(self, db, feed_slug):
        db.set_setting('hold_for_review', 'false', is_default=False)
        assert self._resolve(db, feed_slug) is False

    def test_fresh_database_resolves_off(self, temp_db):
        temp_db.create_podcast('fresh-feed', 'https://example.com/f.xml', title='Fresh')
        assert temp_db.hold_for_review_upgrade_done() is True
        assert self._resolve(temp_db, 'fresh-feed') is False

    def test_an_explicit_false_after_a_successful_migration_is_respected(self, temp_db):
        temp_db.set_setting('hold_for_review', 'true', is_default=False)
        temp_db.set_setting('hold_for_review', 'false', is_default=False)
        assert self._resolve(temp_db, None) is False

    def test_a_per_feed_override_is_respected_while_the_marker_is_absent(self, db, feed_slug):
        db.update_podcast(feed_slug, hold_for_review=0)
        self._drop_marker(db)
        try:
            assert self._resolve(db, feed_slug) is False
        finally:
            db._run_hold_for_review_upgrade_migration(db.get_connection())

    def test_the_settings_api_shows_on_while_the_marker_is_absent(self, client, db, feed_slug):
        db.set_setting('hold_for_review', '0', is_default=True)
        self._drop_marker(db)
        try:
            assert client.get('/api/v1/settings').get_json()['holdForReview']['value'] is True
        finally:
            db._run_hold_for_review_upgrade_migration(db.get_connection())
