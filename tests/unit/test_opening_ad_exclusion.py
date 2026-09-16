"""Opening-window exclusion for ad markers."""
from tests.app_bootstrap import bootstrap

bootstrap('opening_ad_exclusion_test_')

from main_app.processing import _exclude_opening_ads  # noqa: E402


def test_excludes_ads_starting_in_opening_window():
    ads = [
        {'start': 30.0, 'end': 60.0},
        {'start': 119.9, 'end': 180.0},
        {'start': 120.0, 'end': 150.0},
    ]

    assert _exclude_opening_ads(ads, 120.0) == [ads[2]]
    assert _exclude_opening_ads(ads, 0) == ads
