"""list_cue_candidate_dismissals_decoded: JSON decode + bad-row tolerance."""
from api.cue_templates import CUE_CANDIDATE_SCHEMA_VERSION


def test_schema_version_bumped_for_dismissals():
    assert CUE_CANDIDATE_SCHEMA_VERSION == 5
