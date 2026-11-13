"""Per-pass prompt overrides (#429).

An empty override leaves the prompt byte-identical; a set one is appended under a header, or inserted
verbatim at an {override} placeholder when the prompt has one.
"""
from utils.prompt import apply_override, OVERRIDE_HEADER


class TestApplyOverride:
    def test_empty_is_byte_identical(self):
        prompt = "Analyze this transcript.\n\nOUTPUT FORMAT: ...{sponsor_database}"
        assert apply_override(prompt, "") == prompt
        assert apply_override(prompt, None) == prompt
        assert apply_override(prompt, "   \n ") == prompt

    def test_appends_with_header_when_no_placeholder(self):
        prompt = "Analyze this transcript."
        assert apply_override(prompt, "Be stricter on intros.") == \
            prompt + OVERRIDE_HEADER + "Be stricter on intros."

    def test_inserts_verbatim_at_placeholder(self):
        # At an explicit placeholder the text goes in raw, with no header that could countermand the
        # surrounding wording.
        prompt = "Rules.\n{override}\nOutput: []"
        out = apply_override(prompt, "Keep cooking-segment sponsors.")
        assert out == "Rules.\nKeep cooking-segment sponsors.\nOutput: []"
        assert OVERRIDE_HEADER not in out

    def test_empty_strips_leftover_placeholder(self):
        # A customized prompt with {override} but no override set must not ship the
        # literal placeholder to the model.
        prompt = "Rules.\n{override}\nOutput: []"
        assert apply_override(prompt, "") == "Rules.\n\nOutput: []"

    def test_default_prompt_unchanged_end_to_end(self):
        from utils.constants import DEFAULT_SYSTEM_PROMPT
        assert apply_override(DEFAULT_SYSTEM_PROMPT, "") == DEFAULT_SYSTEM_PROMPT
