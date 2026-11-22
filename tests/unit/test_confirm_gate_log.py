"""A confirmed ad that fails the learning gates logs one line that names its span."""
import logging
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('confirm_gate_log_test_')
from api import patterns  # noqa: E402

AD_TEXT = 'This episode is brought to you by Acme. ' * 3


def _confirm(start, end, caplog):
    db = MagicMock()
    db.find_pattern_by_text.return_value = None
    with patch.object(patterns, 'TextPatternMatcher') as matcher:
        matcher.return_value.create_pattern_from_ad.return_value = None
        patterns._resolve_or_create_pattern_from_text(
            db, MagicMock(), 'example-podcast', 'a1b2c3d4e5f6', AD_TEXT,
            {'start': start, 'end': end, 'sponsor': 'Acme'}, label='confirmed')
    return [r.getMessage() for r in caplog.records if 'No pattern created' in r.getMessage()]


def test_each_ad_in_a_request_gets_a_distinct_line(caplog):
    caplog.set_level(logging.INFO)
    _confirm(10.0, 40.0, caplog)
    lines = _confirm(300.0, 330.0, caplog)
    assert len(lines) == 2
    assert len(set(lines)) == 2
    assert '10.0-40.0' in lines[0] and '300.0-330.0' in lines[1]
