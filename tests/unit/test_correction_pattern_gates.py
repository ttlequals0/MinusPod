"""A confirmed span must not mint a pattern the auto path would refuse.

Confirming an ad is a statement about one episode. Turning that span into a
pattern generalizes it to every future episode, and the correction path used
to do that with no gates at all: a 176s confirm whose first 87s were show
content produced a pattern keyed on the content, which then dragged the same
overshoot onto later episodes.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('corr_gate_test_')
from main_app import app

SLUG = 'gate-test-podcast'
EPISODE_ID = 'abc123def002'

# A real read: the brand opens it and repeats, and it fits the duration window.
CLEAN_AD = (
    "[00:00:00.000 --> 00:01:00.000] ExampleSponsor makes everything faster. "
    "Visit examplesponsor.com slash podcast for fifty percent off. "
    "ExampleSponsor, better than the competition."
)

# The shape that produced the bad pattern: minutes of conversation, with the
# read only arriving at the end.
CONTENT_THEN_AD = (
    "[00:00:00.000 --> 00:03:00.000] It's wonderful. I don't think Hamilton "
    "ever wrote anything short, and by the way you won't be buying that in the "
    "iBookstore, I don't think. So we were talking about the browser thing and "
    "how it all shook out last week, which was quite a saga honestly. "
    "ExampleSponsor makes everything faster. Visit examplesponsor.com."
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _mock_db(transcript):
    db = MagicMock()
    db.get_transcript_for_timestamps.return_value = transcript
    db.get_podcast_by_slug.return_value = {'id': 42, 'slug': SLUG}
    db.find_pattern_by_text.return_value = None
    db.get_known_sponsor_by_name.return_value = None
    db.get_ad_patterns.return_value = []
    db.create_ad_pattern.return_value = 1234
    return db


def _confirm(client, db, start, end):
    payload = {
        'type': 'confirm',
        'original_ad': {
            'start': start, 'end': end,
            'sponsor': 'ExampleSponsor', 'reason': 'host-read sponsor',
        },
    }
    with patch('api.patterns.get_database', return_value=db):
        return client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(payload), content_type='application/json',
        )


def test_clean_confirm_still_creates_a_pattern(client):
    db = _mock_db(CLEAN_AD)
    assert _confirm(client, db, 0.0, 60.0).status_code == 200
    db.create_ad_pattern.assert_called_once()


def test_confirm_past_the_duration_ceiling_creates_no_pattern(client):
    """176s is the span that produced the bad pattern in production."""
    db = _mock_db(CONTENT_THEN_AD)
    assert _confirm(client, db, 1191.5, 1367.9).status_code == 200
    db.create_ad_pattern.assert_not_called()


def test_the_correction_is_still_recorded_when_the_pattern_is_refused(client):
    """Refusing to generalize must not refuse the correction itself."""
    db = _mock_db(CONTENT_THEN_AD)
    resp = _confirm(client, db, 1191.5, 1367.9)
    assert resp.status_code == 200
    db.create_pattern_correction.assert_called()
