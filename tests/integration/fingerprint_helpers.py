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

SECOND_TEMPLATE = ('This episode is brought to you by Beta Corp, the better way to bank your money. '
                   'Go to betacorp.com now for a free trial and a bonus offer today.')
FIRST_READ = TEMPLATE + ' Visit acme.com today.'
TWO_SPONSORS = FIRST_READ + ' ' + SECOND_TEMPLATE


def seed_timed_transcript(db, timed_lines):
    """Store a transcript from (start, end, text) lines."""
    def stamp(sec):
        return f'{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:06.3f}'
    db.save_original_transcript(SLUG, EPISODE, '\n'.join(
        f'[{stamp(a)} --> {stamp(b)}] {text}' for a, b, text in timed_lines))


def seed_two_sponsor_transcript(db, first=(10.0, 40.0), second=(40.0, 70.0)):
    """Store a transcript whose two timed lines are the two sponsor reads."""
    seed_timed_transcript(db, [(*first, FIRST_READ), (*second, SECOND_TEMPLATE)])
