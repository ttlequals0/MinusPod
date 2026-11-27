"""Keep-content windows in json_object mode ask for spans under a neutral "content" key.

Reusing the "ads" key steered models toward listing ad spans, which keep-content then
inverts, cutting show audio and keeping ads.
"""
import json
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap
bootstrap('keep_content_json_object_envelope_test_')

import ad_detector
from ad_detector import AdDetector
from ad_detector.prompts import JSON_OBJECT_ADS_INSTRUCTION, json_object_list_instruction
from utils import llm_call

WINDOW = {'start': 0.0, 'end': 100.0,
          'segments': [{'start': 0.0, 'end': 5.0, 'text': 'Hello'}]}


class _Resp:
    def __init__(self, content):
        self.content = content
        self.usage = {}


def _run_window(schema_supported, content=None):
    """Drive _process_keep_content_window through the real _call_llm_for_window."""
    sent = []

    def fake_window_call(**kw):
        sent.append(kw)
        if content is None:
            return None, RuntimeError('not sent')
        return _Resp(content), None

    detector = AdDetector.__new__(AdDetector)
    detector._llm_client_override = MagicMock()
    with patch.object(llm_call, 'supports_json_schema_for_calls', return_value=schema_supported), \
            patch.object(ad_detector, 'call_llm_for_window', side_effect=fake_window_call), \
            patch.object(ad_detector, 'live_route_params') as live_params:
        live_params.return_value = MagicMock(
            provider='openrouter', credential_slot='primary', model='example-model',
            timeout=1, max_retries=0, route=None)
        result = detector._process_keep_content_window(
            window_idx=0, window=WINDOW, total_windows=1, model='example-model',
            podcast_name='example-podcast', episode_title='Episode',
            description_section='', llm_timeout=1, max_retries=0,
            slug='example-podcast', episode_id='a1b2c3d4e5f6')
    assert len(sent) == 1
    return sent[0], result


def test_json_object_keep_content_prompt_uses_content_key():
    kw, _ = _run_window(schema_supported=False)
    assert kw['response_format'] == {'type': 'json_object'}
    assert '{"content": [' in kw['prompt']
    assert '{"content": []}' in kw['prompt']
    assert '"ads"' not in kw['prompt']


def test_schema_keep_content_prompt_is_unchanged():
    kw, _ = _run_window(schema_supported=True)
    assert kw['response_format']['type'] == 'json_schema'
    assert 'RESPONSE SHAPE' not in kw['prompt']


def test_ads_instruction_text_is_stable():
    assert JSON_OBJECT_ADS_INSTRUCTION == json_object_list_instruction('ads')
    assert json_object_list_instruction('content') == JSON_OBJECT_ADS_INSTRUCTION.replace(
        'ads', 'content')


def test_content_object_response_yields_every_span():
    body = json.dumps({'content': [{'start': 10, 'end': 50}, {'start': 60, 'end': 90}]})
    _, result = _run_window(schema_supported=False, content=body)
    assert result.ads == [{'start': 10.0, 'end': 50.0}, {'start': 60.0, 'end': 90.0}]


def test_bare_array_response_still_parses():
    body = json.dumps([{'start': 10, 'end': 50}, {'start': 60, 'end': 90}])
    _, result = _run_window(schema_supported=False, content=body)
    assert result.ads == [{'start': 10.0, 'end': 50.0}, {'start': 60.0, 'end': 90.0}]
