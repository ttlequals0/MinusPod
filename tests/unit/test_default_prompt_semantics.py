"""tests/unit/test_default_prompt_semantics.py"""
from tests.app_bootstrap import bootstrap
bootstrap('default_prompt_semantics_test_')

from utils.constants import DEFAULT_SYSTEM_PROMPT


def test_paid_cross_promo_is_sponsor():
    low = DEFAULT_SYSTEM_PROMPT.lower()
    assert "paid" in low and "cross_promo" in low
    assert "unpaid" in low
