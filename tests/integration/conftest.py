"""Shared fixtures for integration tests."""
import pytest


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    # Feed-mutating endpoints require an app password to be configured.
    db.set_setting('app_password', generate_password_hash('OverrideTest123!', method='scrypt'))
    slug = 'override-endpoint-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Source Show')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        # Restore the no-password state so this doesn't leak into other
        # integration tests that share the app_client database.
        db.set_setting('app_password', '')


@pytest.fixture
def stub_fingerprinting(monkeypatch):
    """fpcalc found, generation stubbed: no audio is decoded."""
    from audio_fingerprinter import AudioFingerprint, AudioFingerprinter
    calls = []

    def generate(self, audio_path, start=0, duration=None):
        calls.append((audio_path, start, duration))
        return AudioFingerprint(fingerprint=f'AQAA{start}', duration=duration)
    monkeypatch.setattr(AudioFingerprinter, '_find_fpcalc', lambda self: '/usr/bin/fpcalc')
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint', generate)
    return calls


@pytest.fixture
def fp_feed(app_client):
    """A feed with one processed episode; the database handle."""
    from api import get_database
    from tests.integration.fingerprint_helpers import EPISODE, SLUG
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE, original_url='https://example.com/ep.mp3',
                      title='Episode One', original_duration=300.0, status='processed')
    db.get_connection().execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE,))
    db.get_connection().commit()
    yield db
    db.delete_podcast(SLUG)


@pytest.fixture
def fp_original(fp_feed):
    """The episode keeps its original audio on disk."""
    from api import get_storage
    from tests.integration.fingerprint_helpers import EPISODE, SLUG
    path = get_storage().get_original_path(SLUG, EPISODE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ID3')
    fp_feed.upsert_episode(SLUG, EPISODE, original_file=path.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def fp_pattern(fp_feed):
    from tests.integration.fingerprint_helpers import SLUG, TEMPLATE
    return fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE)


@pytest.fixture
def fp_audio_original(fp_feed, tmp_path):
    """The episode keeps a real 12 s mp3 original on disk."""
    import shutil
    import subprocess
    from api import get_storage
    from tests.integration.fingerprint_helpers import EPISODE, SLUG
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is not installed')
    path = get_storage().get_original_path(SLUG, EPISODE)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=12',
                    '-c:a', 'libmp3lame', str(path)], check=True, capture_output=True)
    fp_feed.upsert_episode(SLUG, EPISODE, original_file=path.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def fp_audio_pattern(fp_feed, fp_pattern):
    """A pattern whose fingerprint was built from 2 s to 8 s of the episode."""
    from tests.integration.fingerprint_helpers import EPISODE, SLUG
    fp_feed.create_audio_fingerprint(
        fp_pattern, b'AQAA', 6.0, source_podcast_slug=SLUG, source_episode_id=EPISODE,
        source_start=2.0, source_end=8.0)
    return fp_pattern
