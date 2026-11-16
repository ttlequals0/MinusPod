"""Constants and helpers for the correction and fingerprint integration tests."""
from tests.app_bootstrap import bootstrap

bootstrap('fingerprint_correction_test_', reset_storage=True)

SLUG = 'fingerprint-correction-feed'
EPISODE = 'a1b2c3d4e5f6'
OTHER = 'b2c3d4e5f6a1'
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


def stamp(sec):
    return f'{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:06.3f}'


def seed_timed_transcript(db, timed_lines, episode_id=EPISODE):
    """Store a transcript from (start, end, text) lines."""
    db.save_original_transcript(SLUG, episode_id, '\n'.join(
        f'[{stamp(a)} --> {stamp(b)}] {text}' for a, b, text in timed_lines))


def retain_original(db, episode_id=EPISODE):
    """Put a stub original on disk for the episode and record it; returns the path."""
    from api import get_storage
    path = get_storage().get_original_path(SLUG, episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ID3')
    db.upsert_episode(SLUG, episode_id, original_file=path.name)
    return path


def seed_other_episode(db, lines, retained=True):
    """A second processed episode with a timed transcript, and a retained original unless retained is False."""
    db.upsert_episode(slug=SLUG, episode_id=OTHER, original_url='https://example.com/b.mp3',
                      title='Episode Two', original_duration=300.0, status='processed')
    seed_timed_transcript(db, lines, OTHER)
    return retain_original(db, OTHER) if retained else None


def seed_two_sponsor_transcript(db, first=(10.0, 40.0), second=(40.0, 70.0)):
    """Store a transcript whose two timed lines are the two sponsor reads."""
    seed_timed_transcript(db, [(*first, FIRST_READ), (*second, SECOND_TEMPLATE)])
