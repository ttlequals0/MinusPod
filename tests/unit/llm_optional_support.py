"""Shared fixtures for the LLM-optional settings tests."""
from tests.app_bootstrap import authenticate_test_client
from api import get_database
from llm_client import invalidate_provider_cache


def restoring_auth(app_client):
    """Yield auth headers, then restore every settings row the test touched."""
    db = get_database()
    conn = db.get_connection()
    saved = {r['key']: (r['value'], r['is_default'])
             for r in conn.execute('SELECT key, value, is_default FROM settings')}
    db.set_setting('reviewer_calibration_on_change', 'false', is_default=False)
    token = authenticate_test_client(app_client)
    yield token
    for row in conn.execute('SELECT key, value, is_default FROM settings').fetchall():
        if row['key'] not in saved:
            conn.execute('DELETE FROM settings WHERE key = ?', (row['key'],))
        elif saved[row['key']] != (row['value'], row['is_default']):
            conn.execute('UPDATE settings SET value = ?, is_default = ? WHERE key = ?',
                         (*saved[row['key']], row['key']))
    conn.commit()
    invalidate_provider_cache()
