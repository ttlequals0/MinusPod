"""Integration tests for the #521 feed API surface: websiteUrl exposure
and the passthroughEnabled setting round-trip."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pt-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'pt-api-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'PT API Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


def test_feed_exposes_website_url_and_passthrough(app_client, seeded_feed):
    db, slug = seeded_feed['db'], seeded_feed['slug']
    db.update_podcast(slug, website_url='https://www.example.com/',
                      passthrough_enabled=1)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['websiteUrl'] == 'https://www.example.com/'
    assert data['passthroughEnabled'] is True


def test_patch_passthrough_round_trip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'passthroughEnabled': True}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['passthroughEnabled'] is True

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'passthroughEnabled': False}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['passthroughEnabled'] is False


def test_patch_skip_ad_detection_round_trip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is None

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipAdDetection': True}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is True

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipAdDetection': None}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipAdDetection'] is None


def test_patch_skip_second_pass_round_trip(app_client, seeded_feed):
    """Issue #599: per-feed opt-out of the pass-2 verification scan."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipSecondPass': True}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipSecondPass'] is True

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipSecondPass': False}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipSecondPass'] is False

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipSecondPass': None}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['skipSecondPass'] is None


def test_patch_skip_second_pass_rejects_non_bool(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipSecondPass': 'yes'}, headers=headers)
    assert resp.status_code == 400


class TestProcessingModePatch:
    @pytest.mark.parametrize('mode', [
        'passthrough', 'skip_detection', 'keep_content', 'standard'])
    def test_patch_processing_mode_round_trips(self, app_client, seeded_feed, mode):
        slug = seeded_feed['slug']
        _authed(app_client)
        headers = _csrf_headers(app_client)

        resp = app_client.patch(f'/api/v1/feeds/{slug}',
                                json={'processingMode': mode}, headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()['processingMode'] == mode

    def test_preset_overwrites_layered_legacy_flags(self, app_client, seeded_feed):
        # Layer skip under passthrough via legacy fields (issue #537), then
        # a preset write canonicalizes: standard clears both.
        slug = seeded_feed['slug']
        _authed(app_client)
        headers = _csrf_headers(app_client)

        app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'skipAdDetection': True}, headers=headers)
        app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'passthroughEnabled': True}, headers=headers)
        resp = app_client.patch(f'/api/v1/feeds/{slug}',
                                json={'processingMode': 'standard'}, headers=headers)
        body = resp.get_json()
        assert body['processingMode'] == 'standard'
        assert body['passthroughEnabled'] is False
        assert body['skipAdDetection'] is False

    def test_legacy_fields_still_layer(self, app_client, seeded_feed):
        # Legacy per-field semantics unchanged: passthrough off reveals skip.
        slug = seeded_feed['slug']
        _authed(app_client)
        headers = _csrf_headers(app_client)

        app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'skipAdDetection': True, 'passthroughEnabled': True},
                         headers=headers)
        resp = app_client.patch(f'/api/v1/feeds/{slug}',
                                json={'passthroughEnabled': False}, headers=headers)
        assert resp.get_json()['processingMode'] == 'skip_detection'

    def test_mixing_preset_and_legacy_fields_rejected(self, app_client, seeded_feed):
        slug = seeded_feed['slug']
        _authed(app_client)
        headers = _csrf_headers(app_client)

        resp = app_client.patch(
            f'/api/v1/feeds/{slug}',
            json={'processingMode': 'standard', 'passthroughEnabled': True},
            headers=headers)
        assert resp.status_code == 400
        assert 'processingMode' in resp.get_json()['error']

    def test_invalid_preset_rejected(self, app_client, seeded_feed):
        slug = seeded_feed['slug']
        _authed(app_client)
        headers = _csrf_headers(app_client)

        resp = app_client.patch(f'/api/v1/feeds/{slug}',
                                json={'processingMode': 'bogus'}, headers=headers)
        assert resp.status_code == 400


def _add_template(db, slug, cue_type, enabled=1):
    podcast = db.get_podcast_by_slug(slug)
    template_id = db.create_cue_template(
        podcast_id=podcast['id'], cue_type=cue_type,
        source_episode_id='ep1', source_offset_s=1.0, duration_s=1.0,
        sample_rate=22050, n_coeffs=13, mfcc_blob=b'\x00' * 52)
    if not enabled:
        db.update_cue_template(template_id, enabled=False)
    return template_id


class TestCueOnlyPatch:
    def test_cue_only_rejected_without_role_typed_templates(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']
        _authed(client)
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'processingMode': 'cue_only'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 400
        assert 'ad-break start' in resp.get_json()['error']

    def test_cue_only_rejected_with_boundary_only(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']; db = seeded_feed['db']
        _authed(client)
        _add_template(db, slug, 'ad_break_boundary')
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'processingMode': 'cue_only'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 400

    def test_cue_only_accepted_with_start_and_end(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']; db = seeded_feed['db']
        _authed(client)
        _add_template(db, slug, 'ad_break_start')
        _add_template(db, slug, 'ad_break_end')
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'processingMode': 'cue_only'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 200
        assert resp.get_json()['processingMode'] == 'cue_only'

    def test_disabled_templates_do_not_count(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']; db = seeded_feed['db']
        _authed(client)
        _add_template(db, slug, 'ad_break_start', enabled=0)
        _add_template(db, slug, 'ad_break_end')
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'processingMode': 'cue_only'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 400


class TestCueOnlySafetyAndTranscription:
    def _enable_cue_only(self, client, slug, db):
        _add_template(db, slug, 'ad_break_start')
        _add_template(db, slug, 'ad_break_end')
        client.patch(f'/api/v1/feeds/{slug}',
                     json={'processingMode': 'cue_only'},
                     headers=_csrf_headers(client))

    def test_safety_round_trip_and_validation(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']
        _authed(client)
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'cueOnlySafety': 'auto_cut'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 200
        assert resp.get_json()['cueOnlySafety'] == 'auto_cut'
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'cueOnlySafety': 'bogus'},
                            headers=_csrf_headers(client))
        assert resp.status_code == 400
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'cueOnlySafety': None},
                            headers=_csrf_headers(client))
        assert resp.status_code == 200
        assert resp.get_json()['cueOnlySafety'] is None

    def test_skip_transcription_requires_cue_only(self, app_client, seeded_feed):
        client = app_client; slug = seeded_feed['slug']; db = seeded_feed['db']
        _authed(client)
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipTranscription': True},
                            headers=_csrf_headers(client))
        assert resp.status_code == 400
        self._enable_cue_only(client, slug, db)
        resp = client.patch(f'/api/v1/feeds/{slug}',
                            json={'skipTranscription': True},
                            headers=_csrf_headers(client))
        assert resp.status_code == 200
        assert resp.get_json()['skipTranscription'] is True
