"""Tests for SponsorService.apply_transcript_corrections.

Display corrections preserve casing and whitespace outside the matched span.
Opt-in is by convention: the replacement string must contain at least one
uppercase character. Lowercase-only replacements (matcher canonicalizations
like 'ag1') are not applied here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sponsor_service import SponsorService


class TestApplyTranscriptCorrections:
    def test_seeded_wegovy_correction_fires(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        assert svc.apply_transcript_corrections("Talk about WeGoV here.") == \
            "Talk about Wegovy here."

    def test_multi_word_wegovy_correction_fires(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        assert svc.apply_transcript_corrections("And we go v in Q3.") == \
            "And Wegovy in Q3."

    def test_does_not_clobber_unrelated_lowercase_canonicalizations(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        # 'AG1', 'BetterHelp', etc are lowercase-only seeds (matcher inputs)
        # and must not get re-cased or otherwise touched here.
        sentence = "Try AG1 from Athletic Greens or BetterHelp today."
        assert svc.apply_transcript_corrections(sentence) == sentence

    def test_preserves_surrounding_casing_and_whitespace(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        # Sentence-leading capitalization and trailing punctuation stay intact.
        original = "Today's Sponsor: WeGoV.    Stay tuned."
        assert svc.apply_transcript_corrections(original) == \
            "Today's Sponsor: Wegovy.    Stay tuned."

    def test_empty_input_returns_empty(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        assert svc.apply_transcript_corrections("") == ""
        assert svc.apply_transcript_corrections(None) is None

    def test_no_match_returns_input_unchanged(self, temp_db):
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        original = "Nothing relevant in this sentence."
        assert svc.apply_transcript_corrections(original) is original or \
            svc.apply_transcript_corrections(original) == original

    def test_invalid_regex_is_skipped_not_fatal(self, temp_db):
        # Insert a deliberately broken regex with a mixed-case replacement
        # and confirm apply_transcript_corrections still works for the rest.
        temp_db.create_sponsor_normalization(
            pattern=r"[unclosed",
            replacement="Whatever",
            category="phrase",
        )
        svc = SponsorService(temp_db)
        svc.seed_initial_data()

        # Should not raise; still corrects WeGoV from the seed.
        assert svc.apply_transcript_corrections("WeGoV again") == "Wegovy again"
