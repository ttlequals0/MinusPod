"""Per-podcast opening ad-exclusion resolution."""
from tests.app_bootstrap import bootstrap

bootstrap('intro_exclusion_resolver_test_')

from config import resolve_ad_detection_exclude_start_seconds  # noqa: E402


class _DB:
    def __init__(self, override, global_value=120.0):
        self.override = override
        self.global_value = global_value

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {'ad_detection_exclude_start_override': self.override}

    def get_setting_float(self, key, default):
        assert key == 'ad_detection_exclude_start_seconds'
        return self.global_value


def test_feed_override_inherits_disables_or_replaces_global():
    assert resolve_ad_detection_exclude_start_seconds(_DB(None), 1) == 120.0
    assert resolve_ad_detection_exclude_start_seconds(_DB(0), 1) == 0.0
    assert resolve_ad_detection_exclude_start_seconds(_DB(45), 1) == 45.0
