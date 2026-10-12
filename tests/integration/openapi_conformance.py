"""Validate live API responses against openapi.yaml (OpenAPI 3.1 = JSON Schema 2020-12)."""
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_PATH = Path(__file__).resolve().parents[2] / 'openapi.yaml'
SPEC_URI = 'urn:minuspod:openapi'
HTTP_METHODS = ('get', 'put', 'post', 'delete', 'patch', 'head', 'options')


@lru_cache(maxsize=1)
def _spec():
    doc = yaml.safe_load(SPEC_PATH.read_text())
    # YAML parses bare 200: keys as ints; JSON pointers need strings.
    for item in doc['paths'].values():
        for method in HTTP_METHODS:
            op = item.get(method)
            if op and 'responses' in op:
                op['responses'] = {str(k): v for k, v in op['responses'].items()}
    return doc


@lru_cache(maxsize=1)
def _registry():
    resource = Resource.from_contents(_spec(), default_specification=DRAFT202012)
    return Registry().with_resource(SPEC_URI, resource)


def response_schema(method, path_template, status=200):
    paths = _spec()['paths']
    if path_template not in paths or method.lower() not in paths[path_template]:
        raise KeyError(f'{method.upper()} {path_template} is not in openapi.yaml')
    responses = paths[path_template][method.lower()]['responses']
    content = responses.get(str(status), {})
    schema = content.get('content', {}).get('application/json', {}).get('schema')
    if schema is None:
        raise KeyError(f'{method.upper()} {path_template} has no {status} application/json schema')
    return schema


def assert_response_conforms(method, path_template, response, status=200):
    assert response.status_code == status, (
        f'{method.upper()} {path_template} returned {response.status_code}: '
        f'{response.get_data(as_text=True)[:300] if hasattr(response, "get_data") else ""}')
    response_schema(method, path_template, status)
    pointer = (f'#/paths/{_escape(path_template)}/{method.lower()}/responses/{status}'
               f'/content/application~1json/schema')
    validator = Draft202012Validator({'$ref': f'{SPEC_URI}{pointer}'}, registry=_registry())
    errors = sorted(validator.iter_errors(response.get_json()), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f'{method.upper()} {path_template} does not match openapi.yaml:']
        for err in errors[:10]:
            where = '/'.join(str(p) for p in err.absolute_path) or '<root>'
            lines.append(f'  at {where}: {err.message}')
        raise AssertionError('\n'.join(lines))


def _escape(segment):
    return segment.replace('~', '~0').replace('/', '~1')
