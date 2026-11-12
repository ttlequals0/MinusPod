import pathlib
import re

import tests.app_bootstrap  # noqa: F401
import llm_client
from config import PROVIDER_NONE, _validate_llm_provider

SRC = pathlib.Path(__file__).resolve().parents[2] / 'src'
NONE_COMPARISON = re.compile(r"(==|!=|\bis\b)\s*(llm_client\.)?PROVIDER_NONE|PROVIDER_NONE\s*(==|!=)")


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


def test_provider_none_compared_only_in_llm_disabled():
    hits = [(p.relative_to(SRC).as_posix(), i)
            for p in SRC.rglob('*.py')
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if NONE_COMPARISON.search(line)]
    assert [h[0] for h in hits] == ['llm_client.py'], hits
    lines = (SRC / 'llm_client.py').read_text().splitlines()
    start = next(i for i, l in enumerate(lines, 1) if l.startswith('def llm_disabled'))
    assert hits[0][1] in (start + 1, start + 2)


def test_no_literal_none_comparison_of_effective_provider():
    bad = re.compile(r"get_effective_provider\(\).*['\"]none['\"]")
    hits = [f"{p}:{i}" for p in SRC.rglob('*.py')
            for i, line in enumerate(p.read_text().splitlines(), 1) if bad.search(line)]
    assert hits == [], hits
