import csv
import io

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('history_mode_test_')


@pytest.mark.parametrize('path', ['/api/v1/history', '/api/v1/history/export?format=json',
                                 '/api/v1/history/export?format=csv'])
@pytest.mark.parametrize('stats_json,expected', [
    ('{"mode":"chapters"}', 'chapters'), (None, None), ('invalid-json', None),
])
def test_history_and_exports_preserve_run_mode(app_client, temp_db, path, stats_json, expected):
    with app_client.session_transaction() as session:
        session['authenticated'] = True
    podcast_id = temp_db.create_podcast('history-mode', 'https://example.com/feed.xml', 'Example')
    history_id = temp_db.record_processing_history(
        podcast_id, 'history-mode', 'Example', 'a1b2c3d4e5f6', 'Episode', 'completed')
    conn = temp_db.get_connection()
    conn.execute('UPDATE processing_history SET processing_stats_json = ? WHERE id = ?',
                 (stats_json, history_id))
    conn.commit()

    response = app_client.get(path)

    assert response.status_code == 200
    if 'format=csv' in path:
        entries = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        entry = next(row for row in entries if int(row['id']) == history_id)
        assert entry['mode'] == (expected or '')
    else:
        entry = next(row for row in response.get_json()['history'] if row['id'] == history_id)
        assert entry['mode'] == expected
