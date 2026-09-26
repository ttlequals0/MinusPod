"""Pattern list split capability follows the manual split rule."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pattern-split-api-test-'))

from api import get_database  # noqa: E402


ONE_READ = (
    'This episode is brought to you by Acme. Acme makes durable tools '
    'for the workshop and ships every order to your door this week.'
)
TWO_READS = (
    ONE_READ + ' This episode is sponsored by Beta Corp. Beta Corp helps '
    'teams plan their next project and offers a trial this month.'
)


def test_pattern_list_marks_only_splittable_active_rows(app_client):
    db = get_database()
    one_id = db.create_ad_pattern(scope='global', text_template=ONE_READ)
    two_id = db.create_ad_pattern(scope='global', text_template=TWO_READS)
    inactive_id = db.create_ad_pattern(scope='global', text_template=TWO_READS)
    db.update_ad_pattern(inactive_id, is_active=0)

    with app_client.session_transaction() as session:
        session['authenticated'] = True
    response = app_client.get('/api/v1/patterns?active=false')

    assert response.status_code == 200
    rows = {row['id']: row for row in response.get_json()['patterns']}
    assert rows[one_id]['can_split'] is False
    assert rows[two_id]['can_split'] is True
    assert rows[inactive_id]['can_split'] is False
