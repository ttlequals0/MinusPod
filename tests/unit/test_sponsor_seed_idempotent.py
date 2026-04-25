"""Tests for SponsorService.seed_initial_data() idempotency.

Covers the 2.0.13 rewrite that changed seed behavior from "first-run only"
to "name-diff every startup" so that updates to SEED_SPONSORS / SEED_NORMALIZATIONS
auto-propagate to existing deployments without overwriting user-edited rows.
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sponsor_service import SponsorService, SEED_SPONSORS, SEED_NORMALIZATIONS


class TestSeedIdempotent:
    def test_empty_db_seeds_all(self, temp_db):
        svc = SponsorService(temp_db)
        assert temp_db.get_known_sponsors(active_only=False) == []

        svc.seed_initial_data()

        rows = temp_db.get_known_sponsors(active_only=False)
        assert len(rows) == len(SEED_SPONSORS)
        norms = temp_db.get_sponsor_normalizations(active_only=False)
        assert len(norms) == len(SEED_NORMALIZATIONS)

    def test_partial_db_inserts_only_missing(self, temp_db):
        temp_db.create_known_sponsor(name='BetterHelp', aliases=[], category='health')
        temp_db.create_known_sponsor(name='HelloFresh', aliases=[], category='food')
        pre = len(temp_db.get_known_sponsors(active_only=False))
        assert pre == 2

        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        rows = temp_db.get_known_sponsors(active_only=False)
        assert len(rows) == len(SEED_SPONSORS)
        names = {r['name'] for r in rows}
        assert {'BetterHelp', 'HelloFresh'} <= names

    def test_user_edited_aliases_preserved(self, temp_db):
        temp_db.create_known_sponsor(
            name='BetterHelp',
            aliases=['BH', 'Therapy App', 'My Custom Alias'],
            category='health',
        )

        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        row = next(r for r in temp_db.get_known_sponsors(active_only=False) if r['name'] == 'BetterHelp')
        aliases = json.loads(row['aliases']) if isinstance(row['aliases'], str) else row['aliases']
        assert aliases == ['BH', 'Therapy App', 'My Custom Alias']

    def test_deactivated_row_not_reactivated(self, temp_db):
        sponsor_id = temp_db.create_known_sponsor(name='BetterHelp', aliases=[], category='health')
        temp_db.update_known_sponsor(sponsor_id, is_active=0)

        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        row = next(r for r in temp_db.get_known_sponsors(active_only=False) if r['name'] == 'BetterHelp')
        assert row['is_active'] == 0

    def test_running_twice_is_a_noop(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()
        first_count = len(temp_db.get_known_sponsors(active_only=False))

        svc.seed_initial_data()
        second_count = len(temp_db.get_known_sponsors(active_only=False))

        assert first_count == second_count == len(SEED_SPONSORS)
