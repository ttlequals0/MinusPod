"""CUE_CANDIDATE_SCHEMA_VERSION sentinel: bumped to 5 for candidate dismissals (2.44.0)."""
from api.cue_templates import CUE_CANDIDATE_SCHEMA_VERSION


def test_schema_version_bumped_for_dismissals():
    assert CUE_CANDIDATE_SCHEMA_VERSION == 5
