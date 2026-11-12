"""Constants and helpers for the correction and fingerprint integration tests."""
from tests.app_bootstrap import bootstrap

bootstrap('fingerprint_correction_test_', reset_storage=True)

SLUG = 'fingerprint-correction-feed'
EPISODE = 'a1b2c3d4e5f6'
TEMPLATE = 'This episode is brought to you by Acme, the easy way to file your taxes online.'


def csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def post_correction(app_client, body, slug=SLUG, episode=EPISODE):
    return app_client.post(f'/api/v1/episodes/{slug}/{episode}/corrections',
                           json=body, headers=csrf(app_client))
