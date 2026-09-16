"""Hosting-platform names screen new labels, not stored registry rows.

A row named after a hosting platform predates the create-time gate or was
added deliberately. Dropping it at cache-build time stops it matching at all,
so every span it names reads as having no advertiser.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sponsor_normalize import get_or_create_known_sponsor
from sponsor_service import SponsorService


class TestAStoredHostingRowStillMatches:
    def _service(self, temp_db, name):
        temp_db.create_known_sponsor(name=name)
        return SponsorService(temp_db)

    def test_it_compiles_into_the_brand_matcher(self, temp_db):
        svc = self._service(temp_db, 'Anchor')

        assert 'Anchor' in [row['name'] for row in svc.brand_rows()]

    def test_it_matches_a_mention_on_word_boundaries(self, temp_db):
        svc = self._service(temp_db, 'Anchor')

        assert svc.find_sponsor_in_text('this read is from Anchor') == 'Anchor'
        assert svc.brand_mention_offsets('Anchor and Anchor')['Anchor'] == [0, 11]

    def test_a_longer_word_containing_it_is_not_a_mention(self, temp_db):
        svc = self._service(temp_db, 'Anchor')

        assert svc.brand_mention_offsets('the anchorage report') == {}


class TestANewHostingLabelIsStillRefused:
    def test_the_registry_does_not_learn_one(self, temp_db):
        assert get_or_create_known_sponsor(temp_db, 'SoundCloud') is None
        assert temp_db.get_known_sponsor_by_name('SoundCloud') is None
