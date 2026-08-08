"""Tests for segments-wrapped LLM responses parsing ads without type field."""
from tests.app_bootstrap import bootstrap
bootstrap('llm_response_segments_')

import json
from utils.llm_response import extract_json_ads_array

SEG = {'start': 10.0, 'end': 40.0, 'confidence': 0.9,
       'category': 'sponsor', 'reason': 'Acme read'}


def test_untyped_segments_are_ads():
    ads, method = extract_json_ads_array(json.dumps({'segments': [SEG]}), 's', 'e')
    assert ads == [SEG]
    assert method == 'json_object_segments_key'


def test_content_typed_segment_filtered():
    content = {**SEG, 'type': 'content'}
    ads, _ = extract_json_ads_array(json.dumps({'segments': [SEG, content]}), 's', 'e')
    assert ads == [SEG]


def test_window_wrapped_segments_are_ads():
    ads, method = extract_json_ads_array(
        json.dumps({'window': {'segments': [SEG]}}), 's', 'e')
    assert ads == [SEG]
