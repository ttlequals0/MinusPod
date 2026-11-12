"""Stored error text from older builds does not leave the API before the redaction step has run."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf
import api.pending_upgrade_redaction as guard  # noqa: E402

GATE = 'redact_stored_error_text'
RAW = 'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed'
CLEAN = 'GET https://<redacted>@cdn.example.com/a.mp3?<redacted> failed'


@pytest.fixture
def old_error(fp_feed):
    conn = fp_feed.get_connection()
    conn.execute("UPDATE episodes SET status = 'failed', error_message = ? WHERE episode_id = ?", (RAW, EPISODE))
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.execute("UPDATE deferred_upgrade_steps SET state = 'pending' WHERE name = ?", (GATE,))
    conn.commit()
    guard.reset_latch()
    yield fp_feed
    conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (GATE,))
    conn.commit()
    guard.reset_latch()


def _listed_error(client):
    body = client.get(f'/api/v1/feeds/{SLUG}/episodes', headers=csrf(client)).get_json()
    return [e for e in body['episodes'] if e['id'] == EPISODE][0]['error']


def test_an_old_error_is_redacted_on_the_way_out_until_the_step_has_run(app_client, old_error):
    assert old_error.get_connection().execute(
        "SELECT error_message FROM episodes WHERE episode_id = ?", (EPISODE,)).fetchone()[0] == RAW
    assert _listed_error(app_client) == CLEAN


def test_once_the_step_has_run_the_stored_text_is_served_as_is(app_client, old_error):
    assert old_error.run_deferred_upgrade_steps()
    assert old_error.get_connection().execute(
        "SELECT error_message FROM episodes WHERE episode_id = ?", (EPISODE,)).fetchone()[0] == CLEAN
    assert _listed_error(app_client) == CLEAN
    assert guard.latched()
