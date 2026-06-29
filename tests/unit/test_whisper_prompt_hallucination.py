"""Regression tests for Whisper initial-prompt vocabulary hallucination.

Episode 0e8664b8ab86 (an aviation podcast) had "Wegovy, Ozempic, Mounjaro"
emitted verbatim into silent gaps -- Whisper regurgitating its own
initial_prompt -- which the ad detector then cut as phantom pharmaceutical ads.

The fix has three parts, each pinned below:
  1. GLP-1 drug names (and other bare common-English words) are not seeded into
     the prompt, so Whisper is not primed to emit them.
  2. The prompt vocabulary and the hallucination scrubber derive from one list,
     so a seeded term can never be missing from the scrubber.
  3. The scrubber scrubs a segment that is essentially a list of seeded terms
     regardless of length, but keeps a genuine sponsor mention that carries real
     speech around the brand.
"""
import transcriber


GLP1_DRUGS = ("wegovy", "ozempic", "mounjaro")
# Brand entries that are also ordinary English words; seeding/scrubbing these
# deletes real speech ("Stay calm.", "Indeed.", "barely audible").
COMMON_WORD_BRANDS = ("calm", "indeed", "audible")


def _scrub(text):
    """Run one segment through the real filter; return the kept segments."""
    return transcriber.Transcriber().filter_hallucinations([{"text": text}])


class TestPromptDoesNotSeedEditorialWords:
    def test_ad_vocabulary_excludes_glp1_drugs(self):
        vocab = transcriber.AD_VOCABULARY.lower()
        for drug in GLP1_DRUGS:
            assert drug not in vocab, f"{drug} is seeded into the Whisper prompt"

    def test_ad_vocabulary_excludes_common_english_words(self):
        terms = [t.lower() for t in transcriber.AD_VOCABULARY_TERMS]
        for word in COMMON_WORD_BRANDS:
            assert word not in terms, (
                f"{word!r} is a common English word; seeding it makes the "
                f"scrubber delete real speech"
            )

    def test_initial_prompt_excludes_glp1_drugs(self):
        prompt = transcriber.Transcriber().get_initial_prompt(
            "Simple Flying Aviation News"
        ).lower()
        for drug in GLP1_DRUGS:
            assert drug not in prompt


class TestScrubberCoversWhatIsSeeded:
    def test_every_seeded_term_is_actually_scrubbed(self):
        # No-drift invariant, exercised through the real filter (not just a
        # regex.search of the list against itself): each seeded term, echoed
        # alone into a gap, is removed.
        not_scrubbed = [
            term for term in transcriber.AD_VOCABULARY_TERMS if _scrub(term) != []
        ]
        assert not_scrubbed == [], f"seeded terms not scrubbed: {not_scrubbed}"

    def test_empty_vocabulary_list_scrubs_nothing(self):
        # An empty term list must compile to a never-match pattern, not "()"
        # (which matches every string and would blank the transcript).
        pattern = transcriber._compile_vocabulary_pattern([])
        assert pattern.search("Wegovy and anything at all") is None


class TestScrubsRegurgitationButKeepsRealSpeech:
    def test_long_vocabulary_run_is_scrubbed(self):
        # A long comma-run of seeded brands is prompt regurgitation regardless
        # of length (the original bug, generalized past the 100-char gate).
        run = ("Athletic Greens, AG1, BetterHelp, Squarespace, NordVPN, "
               "ExpressVPN, HelloFresh, Masterclass, ZipRecruiter")
        assert len(run) > 100
        assert _scrub(run) == []

    def test_single_brand_regurgitation_is_scrubbed(self):
        assert _scrub("NordVPN") == []

    def test_short_genuine_sponsor_mention_is_kept(self):
        # Real speech that merely names a sponsor carries connective words, so
        # it is not a vocabulary list and must survive.
        assert len(_scrub("I love NordVPN and use it daily")) == 1

    def test_genuine_ad_read_is_kept(self):
        text = ("This episode is brought to you by NordVPN. Use code PODCAST "
                "at checkout and you will get a great deal on your subscription "
                "this month, so go check it out today.")
        assert len(_scrub(text)) == 1

    def test_brand_with_non_latin_speech_is_kept(self):
        # Residue counting must be Unicode-aware: a brand next to non-Latin
        # speech is real content, not a vocabulary list.
        assert len(_scrub("Masterclass " + "你好世界")) == 1
