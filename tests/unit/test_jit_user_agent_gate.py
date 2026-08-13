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
