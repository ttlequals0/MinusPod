import ast
import pathlib
import re

import pytest

import tests.app_bootstrap  # noqa: F401
import llm_client
from config import PROVIDER_NONE, _validate_llm_provider

SRC = pathlib.Path(__file__).resolve().parents[2] / 'src'


def test_provider_none_constant():
    assert PROVIDER_NONE == 'none'


def test_boot_validator_accepts_none():
    assert _validate_llm_provider('none') is True
    assert _validate_llm_provider('bogus') is False


def test_llm_disabled_true_when_provider_none(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')
    assert llm_client.llm_disabled() is True


def test_llm_disabled_false_for_anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')
    assert llm_client.llm_disabled() is False


def _none_comparisons(tree):
    """(line, innermost function name, comparison source) of every comparison using PROVIDER_NONE."""
    hits = []

    def visit(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.Compare):
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            used |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            if 'PROVIDER_NONE' in used:
                hits.append((node.lineno, func, ast.unparse(node)))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, None)
    return hits


def test_provider_none_compared_only_in_llm_disabled():
    found = set()
    for path in SRC.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for _, name, source in _none_comparisons(tree):
            found.add((path.relative_to(SRC).as_posix(), name, source))
    # The boot validator lists none among the accepted values; it does not test the active provider.
    assert found == {('llm_client.py', 'llm_disabled', 'provider == PROVIDER_NONE'),
                     ('config.py', '_validate_llm_provider',
                      'value in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENAI_COMPATIBLE, '
                      'PROVIDER_OLLAMA, PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_NONE)')}, found


def test_scanner_catches_membership_tests():
    tree = ast.parse("def f(p):\n    return p in (PROVIDER_NONE, 'x')\n")
    assert _none_comparisons(tree) == [(2, 'f', "p in (PROVIDER_NONE, 'x')")]


def test_explicit_none_provider_is_not_none():
    assert llm_client.llm_disabled(None) is False
    assert llm_client.llm_disabled('none') is True


def test_no_literal_none_comparison_of_effective_provider():
    bad = re.compile(r"get_effective_provider\(\).*['\"]none['\"]")
    hits = [f"{p}:{i}" for p in SRC.rglob('*.py')
            for i, line in enumerate(p.read_text().splitlines(), 1) if bad.search(line)]
    assert hits == [], hits


def _literal_none_comparisons(tree):
    """(line, function, comparison source) of every comparison with a 'none' string constant."""
    hits = []

    def visit(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.Compare):
            for part in ast.walk(node):
                if isinstance(part, ast.Constant) and part.value == 'none':
                    hits.append((node.lineno, func, ast.unparse(node)))
                    break
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, None)
    return hits


@pytest.mark.parametrize('code', [
    "p == 'none'", "p != \"none\"", "'none' == p", "p in ('x', 'none')", "p not in ['none']",
    "p in {'none'}", "x is 'none'",
])
def test_literal_scanner_catches_every_comparison_shape(code):
    hits = _literal_none_comparisons(ast.parse(f"def f(p):\n    return {code}\n"))
    assert [(line, func) for line, func, _ in hits] == [(2, 'f')]


# (file, function, comparison source): reason the literal 'none' there is not the provider.
_LITERAL_NONE_ALLOWED = {
    ('ad_detector/boundaries.py', '_extract_ad_keywords', "sponsor.lower() not in {'unknown', 'none'}"): 'sponsor name placeholder',
    ('ad_detector/cue_telemetry.py', 'build_cue_detection_records', "outcome == 'none'"): 'cue outcome label',
    ('ad_detector/prompts.py', '_normalize_ad',
     "str(is_ad_val).lower() in ('false', 'no', '0', 'none')"): 'model-returned is_ad value',
    ('api/providers.py', '_provider_status', "source != 'none'"): 'credential source label',
    ('llm_capabilities.py', 'translate_reasoning_effort', "value.lower() == 'none'"): 'reasoning effort level',
    ('llm_capabilities.py', 'translate_reasoning_effort',
     "normalized not in ('none', 'low', 'medium', 'high')"): 'reasoning effort level',
    ('llm_client.py', '_safe_reasoning_value',
     "value.lower() in ('none', 'low', 'medium', 'high')"): 'reasoning effort level',
    ('llm_client.py', '_build_ollama_native_request', "normalized_reasoning in (None, 'none')"): 'reasoning effort level',
    ('main_app/pipeline/signals.py', '_run_transcript_diff', "stored.get('status') != 'none'"): 'differential status',
    ('splice_calibration.py', 'long_cut_corroboration', "source != 'none'"): 'detector source label',
    ('utils/llm_call.py', '_apply_reasoning_fallback', "current in ('low', 'none')"): 'reasoning effort level',
    ('utils/llm_call.py', '_apply_reasoning_fallback',
     "llm_kwargs.get('reasoning_effort') in ('', 'none')"): 'reasoning effort level',
    ('utils/llm_call.py', '_dispatch_on_failover', "reasoning not in (None, 'none')"): 'reasoning effort level',
}


def test_no_literal_none_comparison_outside_llm_disabled():
    found = set()
    for path in SRC.rglob('*.py'):
        for _, name, source in _literal_none_comparisons(ast.parse(path.read_text())):
            found.add((path.relative_to(SRC).as_posix(), name, source))
    found -= {('llm_client.py', 'llm_disabled', "provider == 'none'")}
    assert found == set(_LITERAL_NONE_ALLOWED), found - set(_LITERAL_NONE_ALLOWED)


def test_a_new_comparison_in_an_allowlisted_function_is_not_allowed():
    tree = ast.parse("def translate_reasoning_effort(p):\n    return p == 'none'\n")
    (_, func, source), = _literal_none_comparisons(tree)
    assert ('llm_capabilities.py', func, source) not in _LITERAL_NONE_ALLOWED
