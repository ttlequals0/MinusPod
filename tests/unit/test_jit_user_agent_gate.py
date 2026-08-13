from api.settings import validate_jit_blocked_user_agents
from config import resolve_jit_blocked_user_agents, user_agent_is_jit_blocked


def test_resolve_handles_unset_and_malformed():
    assert resolve_jit_blocked_user_agents(None) == []
    assert resolve_jit_blocked_user_agents('') == []
    assert resolve_jit_blocked_user_agents('not json') == []
    assert resolve_jit_blocked_user_agents('{"a": 1}') == []
    assert resolve_jit_blocked_user_agents('["a", 2, "", "  ", "b"]') == ['a', 'b']


def test_substring_match_is_case_insensitive():
    pats = resolve_jit_blocked_user_agents('["WordPress.com - Audio"]')
    assert user_agent_is_jit_blocked('WordPress.com - Audio/1.0', pats)
    assert user_agent_is_jit_blocked('wordpress.com - audio/1.0', pats)
    assert not user_agent_is_jit_blocked('Pocket Casts', pats)


def test_caret_anchors_to_start():
    pats = resolve_jit_blocked_user_agents('["^atc/"]')
    assert user_agent_is_jit_blocked('atc/1.0', pats)
    assert not user_agent_is_jit_blocked('Snatch/2.1', pats)


def test_empty_list_and_missing_agent_never_block():
    assert not user_agent_is_jit_blocked('anything', [])
    assert not user_agent_is_jit_blocked(None, ['anything'])
    assert not user_agent_is_jit_blocked('', ['anything'])


def test_validator_accepts_a_clean_list():
    value, err = validate_jit_blocked_user_agents(['WordPress.com - Audio', '^atc/'])
    assert err is None
    assert value == ['WordPress.com - Audio', '^atc/']


def test_validator_rejects_non_list_and_bad_entries():
    assert validate_jit_blocked_user_agents('nope')[1] is not None
    assert validate_jit_blocked_user_agents([1])[1] is not None
    assert validate_jit_blocked_user_agents([''])[1] is not None
    assert validate_jit_blocked_user_agents(['x' * 201])[1] is not None


def test_validator_trims_and_drops_blanks():
    value, err = validate_jit_blocked_user_agents(['  Bot  ', '   '])
    assert err is None
    assert value == ['Bot']
