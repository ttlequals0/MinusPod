"""Tests for SponsorService.seed_initial_data() idempotency.

As of 2.4.0 the schema migration is the authoritative seed for sponsors
(loaded from src/seed_data/sponsors_final.csv). SponsorService.seed_initial_data()
still runs at startup and is responsible for normalizations; for sponsors it is
effectively a no-op against the post-migration baseline because every SEED_SPONSORS
name already exists. These tests pin that idempotency contract.
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sponsor_service import SponsorService, SEED_NORMALIZATIONS


class TestSeedIdempotent:
    def test_baseline_db_has_migrated_seed(self, temp_db):
        """The migration runs during Database init and seeds 255 sponsors."""
        rows = temp_db.get_known_sponsors(active_only=False)
        # 255 from sponsors_final.csv (migration v2.4.0). The number is a hard
        # contract: changing it requires bumping the seed revision in schema.py.
        assert len(rows) == 255

    def test_seed_initial_data_is_noop_against_migration_baseline(self, temp_db):
        svc = SponsorService(temp_db)
        before = len(temp_db.get_known_sponsors(active_only=False))
        svc.seed_initial_data()
        after = len(temp_db.get_known_sponsors(active_only=False))
        # SponsorService only inserts names not already present;
        # every SEED_SPONSORS name overlaps with the migration seed.
        assert before == after == 255

    def test_user_edited_aliases_preserved(self, temp_db):
        """Editing a sponsor's aliases after migration is preserved by seed_initial_data."""
        existing = temp_db.get_known_sponsor_by_name('BetterHelp')
        assert existing is not None
        temp_db.update_known_sponsor(
            existing['id'],
            aliases=['BH', 'Therapy App', 'My Custom Alias'],
        )

        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        row = temp_db.get_known_sponsor_by_name('BetterHelp')
        aliases = json.loads(row['aliases']) if isinstance(row['aliases'], str) else row['aliases']
        assert aliases == ['BH', 'Therapy App', 'My Custom Alias']

    def test_deactivated_row_not_reactivated(self, temp_db):
        existing = temp_db.get_known_sponsor_by_name('BetterHelp')
        temp_db.update_known_sponsor(existing['id'], is_active=0)

        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        row = temp_db.get_known_sponsor_by_name('BetterHelp')
        assert row['is_active'] == 0

    def test_running_twice_is_a_noop(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()
        first_count = len(temp_db.get_known_sponsors(active_only=False))

        svc.seed_initial_data()
        second_count = len(temp_db.get_known_sponsors(active_only=False))

        assert first_count == second_count

    def test_normalizations_still_seeded(self, temp_db):
        # Normalizations are unaffected by the migration -- SponsorService remains
        # the only place that seeds them.
        svc = SponsorService(temp_db)
        svc.seed_initial_data()
        norms = temp_db.get_sponsor_normalizations(active_only=False)
        assert len(norms) == len(SEED_NORMALIZATIONS)
