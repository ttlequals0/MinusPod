"""Every route with a flask-limiter decorator documents the shared 429 response."""
import re
from pathlib import Path

import yaml

from tests.app_bootstrap import bootstrap

bootstrap('openapi_429_')

from api import limiter  # noqa: E402
from main_app import app  # noqa: E402

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / 'openapi.yaml').read_text())
API_PREFIX = '/api/v1'
RATE_LIMITED_REF = '#/components/responses/RateLimited'


def _spec_path(rule):
    return re.sub(r'<(?:[^:>]+:)?([^>]+)>', r'{\1}', rule[len(API_PREFIX):])


def _limited_operations():
    decorated = set(limiter.limit_manager._decorated_limits)
    ops = []
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        if not rule.rule.startswith(API_PREFIX + '/'):
            continue
        if f'{view.__module__}.{view.__name__}.{view.__name__}' not in decorated:
            continue
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            ops.append((method.lower(), _spec_path(rule.rule)))
    return sorted(ops)


def _documents_429(response):
    # A route-specific inline 429 is fine when it keeps the shared body schema.
    if response is None:
        return False
    if response.get('$ref') == RATE_LIMITED_REF:
        return True
    schema = response.get('content', {}).get('application/json', {}).get('schema', {})
    refs = [schema.get('$ref')] + [o.get('$ref') for o in schema.get('anyOf', [])]
    return '#/components/schemas/RateLimitError' in refs and 'Retry-After' in response.get('headers', {})


def test_limiter_introspection_finds_decorated_routes():
    assert len(_limited_operations()) >= 40


def test_every_rate_limited_route_documents_429():
    missing = []
    for method, path in _limited_operations():
        op = SPEC['paths'].get(path, {}).get(method)
        if op is None:
            missing.append(f'{method.upper()} {path}: not in spec')
        elif not _documents_429(op.get('responses', {}).get('429')):
            missing.append(f'{method.upper()} {path}')
    assert not missing, missing
