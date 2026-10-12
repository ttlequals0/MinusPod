"""Validate live API responses against openapi.yaml (OpenAPI 3.1 = JSON Schema 2020-12)."""
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SPEC_PATH = Path(__file__).resolve().parents[2] / 'openapi.yaml'


@lru_cache(maxsize=1)
def _spec():
    return yaml.safe_load(SPEC_PATH.read_text())


def response_schema(method, path_template, status=200):
    paths = _spec()['paths']
    if path_template not in paths or method.lower() not in paths[path_template]:
        raise KeyError(f'{method.upper()} {path_template} is not in openapi.yaml')
    responses = paths[path_template][method.lower()]['responses']
    # YAML parses bare 200: keys as ints.
    content = responses.get(status) or responses.get(str(status)) or {}
    schema = content.get('content', {}).get('application/json', {}).get('schema')
    if schema is None:
        raise KeyError(f'{method.upper()} {path_template} has no {status} application/json schema')
    return schema


@lru_cache(maxsize=None)
def _validator(method, path_template, status=200):
    # Root-level components let fragment refs like #/components/schemas/X resolve.
    schema = {**response_schema(method, path_template, status), 'components': _spec()['components']}
    return Draft202012Validator(schema)


def assert_response_conforms(method, path_template, response, status=200):
    assert response.status_code == status, (
        f'{method.upper()} {path_template} returned {response.status_code}: '
        f'{response.get_data(as_text=True)[:300]}')
    validator = _validator(method, path_template, status)
    errors = sorted(validator.iter_errors(response.get_json()), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f'{method.upper()} {path_template} does not match openapi.yaml:']
        for err in errors[:10]:
            where = '/'.join(str(p) for p in err.absolute_path) or '<root>'
            lines.append(f'  at {where}: {err.message}')
        raise AssertionError('\n'.join(lines))
