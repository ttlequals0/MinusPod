"""Issue #695: the review system prompt's examples must show the same shape
_build_user_prompt actually sends, so the model reads trim boundaries off the
timestamps instead of interpolating them."""
import re

from tests.app_bootstrap import bootstrap

bootstrap('review_prompt_examples_test_')

from database import DEFAULT_REVIEW_PROMPT
from main_app import db

_STAMPED_LINE = re.compile(r'^\[\d+\.\d+s-\d+\.\d+s\] ', re.MULTILINE)


def test_examples_use_the_candidate_markers():
    assert DEFAULT_REVIEW_PROMPT.count('>>> CANDIDATE AD START [') == 3
    assert DEFAULT_REVIEW_PROMPT.count('<<< CANDIDATE AD END [') == 3


def test_examples_carry_start_end_stamps_on_every_transcript_line():
    assert len(_STAMPED_LINE.findall(DEFAULT_REVIEW_PROMPT)) >= 15
    # The old single-anchor form is what #695 reported as the mismatch.
    assert not re.search(r'^\[\d+\.\d+s\] ', DEFAULT_REVIEW_PROMPT, re.MULTILINE)


def test_adjust_example_boundaries_appear_in_its_context_lines():
    """95.0 and 132.0 must be readable off the surrounding lines, not guessed."""
    assert '[92.0s-95.0s]' in DEFAULT_REVIEW_PROMPT
    assert '[130.0s-132.0s]' in DEFAULT_REVIEW_PROMPT
    assert '"start": 95.0, "end": 132.0' in DEFAULT_REVIEW_PROMPT


def test_framing_matches_the_user_prompt_wording():
    assert 'Original boundaries:' in DEFAULT_REVIEW_PROMPT
    assert 'Original detection:' not in DEFAULT_REVIEW_PROMPT


def test_migration_refreshes_a_stored_default_but_not_a_customized_prompt():
    conn = db.get_connection()

    conn.execute("UPDATE settings SET value = ?, is_default = 1 WHERE key = 'review_prompt'",
                 ('stale default without the markers',))
    conn.commit()
    db._run_schema_migrations()
    assert 'CANDIDATE AD START' in db.get_setting('review_prompt')

    conn.execute("UPDATE settings SET value = ?, is_default = 0 WHERE key = 'review_prompt'",
                 ('my own prompt',))
    conn.commit()
    db._run_schema_migrations()
    assert db.get_setting('review_prompt') == 'my own prompt'
