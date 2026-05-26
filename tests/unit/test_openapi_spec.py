"""Validate openapi.yaml at parse-time.

Closes the gap that let 2.5.23 ship with four duplicate-key entries in
the ``PUT /settings/ad-detection`` request body (audioBitrate,
skipFlacCompression, adDetectionParallelWindows, adReviewerParallelAds
were each defined twice -- once with the response-side object shape
and once with the request-side plain shape).

PyYAML's default ``SafeLoader.construct_mapping`` silently keeps the
last-occurrence value on duplicate keys, so the bug never surfaced
through normal parsing. The ``StrictDuplicateKeyLoader`` below raises
on duplicates, which is what we want for a spec file.

The spec is also confirmed to be loadable and to expose the expected
top-level shape so a future careless edit (e.g. dropping ``paths:``
or breaking the indentation under ``components.schemas``) fails fast.
"""
from pathlib import Path

import pytest
import yaml


SPEC_PATH = Path(__file__).resolve().parents[2] / 'openapi.yaml'


class _DuplicateKeyError(ValueError):
    """Raised when openapi.yaml contains a duplicate key inside any mapping."""


class StrictDuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate keys inside a mapping.

    Default PyYAML behavior is last-occurrence-wins, which masks bugs in
    spec files where every key is meant to be unique.
    """


def _construct_mapping_no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            mark = key_node.start_mark
            raise _DuplicateKeyError(
                f"Duplicate key {key!r} at line {mark.line + 1}, column {mark.column + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictDuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def test_openapi_yaml_loads():
    """The spec file parses with the standard SafeLoader.

    Smoke test: catches gross YAML syntax breakage immediately.
    """
    with open(SPEC_PATH) as f:
        doc = yaml.safe_load(f)
    assert isinstance(doc, dict)


def test_openapi_yaml_has_no_duplicate_keys():
    """The spec file has no duplicate keys inside any mapping.

    Default PyYAML loaders silently keep the last value on duplicates,
    which is how 2.5.23 shipped with the same key defined twice in the
    PUT request body. This test would have failed on that branch and
    will fail on any future re-introduction.
    """
    with open(SPEC_PATH) as f:
        try:
            yaml.load(f, Loader=StrictDuplicateKeyLoader)
        except _DuplicateKeyError as e:
            pytest.fail(f"openapi.yaml has a duplicate key: {e}")


def test_openapi_has_expected_top_level_shape():
    """Spec exposes the top-level keys downstream consumers rely on.

    Guards against an edit that accidentally renames or drops
    ``paths`` / ``components`` / ``info.version``. The version match
    against ``version.py`` is enforced separately by the
    /build-and-push flow; here we only check the key exists.
    """
    with open(SPEC_PATH) as f:
        doc = yaml.safe_load(f)
    assert doc.get('openapi', '').startswith('3.'), 'expected openapi 3.x spec'
    assert 'info' in doc and isinstance(doc['info'], dict)
    assert doc['info'].get('version'), 'info.version is required'
    assert 'paths' in doc and isinstance(doc['paths'], dict) and doc['paths']
    assert 'components' in doc and isinstance(doc['components'], dict)


def test_strict_loader_actually_catches_duplicate_keys():
    """Meta-test: prove the StrictDuplicateKeyLoader rejects a known
    duplicate. If this regression-checker is itself broken, the real
    duplicate-key test would silently pass on a broken spec."""
    duplicated_yaml = """
    foo:
      key: 1
      key: 2
    """
    with pytest.raises(_DuplicateKeyError, match="Duplicate key 'key'"):
        yaml.load(duplicated_yaml, Loader=StrictDuplicateKeyLoader)


def test_settings_ad_detection_put_body_uses_request_shapes():
    """Sanity check that the PUT request body fields are plain types,
    not the GET response's ``{value, isDefault}`` object shape.

    Regression test for the specific bug PR #287 / s1shed fork reported
    and the three matching duplicates this PR fixes. The
    /settings/ad-detection PUT handler reads e.g. ``data['audioBitrate']``
    directly as a string, so the spec must describe the string shape.
    """
    with open(SPEC_PATH) as f:
        doc = yaml.safe_load(f)
    paths = doc.get('paths', {})
    ad_detection = paths.get('/settings/ad-detection', {})
    put_op = ad_detection.get('put', {})
    schema = (
        put_op.get('requestBody', {})
        .get('content', {})
        .get('application/json', {})
        .get('schema', {})
    )
    props = schema.get('properties', {})

    # Each of these fields, if present, must be a plain primitive
    # (string / boolean / integer) rather than the ``object`` wrapper
    # used in response schemas.
    for field, expected_type in (
        ('audioBitrate', 'string'),
        ('skipFlacCompression', 'boolean'),
        ('adDetectionParallelWindows', 'integer'),
        ('adReviewerParallelAds', 'integer'),
    ):
        if field not in props:
            continue
        actual = props[field].get('type')
        assert actual == expected_type, (
            f"{field}: PUT body expects plain {expected_type!r}, got {actual!r}. "
            f"This usually means the response-shape object block was copied "
            f"into the request body."
        )
