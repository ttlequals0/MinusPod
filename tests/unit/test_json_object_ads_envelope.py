"""Detection windows in json_object mode ask for every ad under an "ads" key.

json_object mode forces a top-level JSON object, while the detection prompts
describe a bare array. Without an explicit envelope the model answered with one
bare ad object per window and dropped the rest. Calls that send a json_schema
already carry the {"ads": [...]} shape and keep their prompt unchanged.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('json_object_ads_envelope_test_')

import ad_detector
from ad_detector import AdDetector, PASS_AD_DETECTION_1, PASS_AD_DETECTION_2
from utils import llm_call
from utils.llm_response import extract_json_ads_array

WINDOW_PROMPT = "Transcript (with [{start}-{end}] timestamp, in seconds):\n[0.0-5.0] Hello"


def _send_window(schema_supported, pass_name=PASS_AD_DETECTION_1, provider='openrouter'):
    sent = []

    def fake_window_call(**kw):
        sent.append(kw)
        return None, RuntimeError('not sent')

    detector = AdDetector.__new__(AdDetector)
    detector._llm_client_override = MagicMock()
    with patch.object(llm_call, 'supports_json_schema_for_calls', return_value=schema_supported), \
            patch.object(ad_detector, 'call_llm_for_window', side_effect=fake_window_call), \
            patch.object(ad_detector, 'live_route_params') as live_params:
        live_params.return_value = MagicMock(
            provider=provider, credential_slot='primary', model='example-model',
            timeout=1, max_retries=0, route=None)
        detector._call_llm_for_window(
            model='example-model', system_prompt='s', prompt=WINDOW_PROMPT,
            llm_timeout=1, max_retries=0, slug='example-podcast',
            episode_id='a1b2c3d4e5f6', window_label='Window 1', pass_name=pass_name)
    assert len(sent) == 1
    return sent[0]


@pytest.mark.parametrize('pass_name', [PASS_AD_DETECTION_1, PASS_AD_DETECTION_2])
@pytest.mark.parametrize('provider', ['openrouter', 'openai-compatible', 'ollama', 'anthropic'])
def test_json_object_window_prompt_asks_for_ads_envelope(pass_name, provider):
    kw = _send_window(schema_supported=False, pass_name=pass_name, provider=provider)
    assert kw['response_format'] == {'type': 'json_object'}
    prompt = kw['prompt']
    assert prompt.startswith(WINDOW_PROMPT)
    assert '{"ads": [' in prompt
    assert '{"ads": []}' in prompt
    assert 'every' in prompt[len(WINDOW_PROMPT):].lower()


def test_json_schema_window_prompt_is_unchanged():
    kw = _send_window(schema_supported=True, provider='openai-compatible')
    assert kw['response_format']['type'] == 'json_schema'
    assert kw['prompt'] == WINDOW_PROMPT


def test_ads_envelope_keeps_every_ad():
    first = {"start": 2253.3, "end": 2264.1, "confidence": 0.99, "category": "sponsor"}
    second = {"start": 2264.1, "end": 2290.0, "confidence": 0.95, "category": "sponsor"}
    ads, method = extract_json_ads_array(json.dumps({"ads": [first, second]}))
    assert ads == [first, second]
    assert method == "json_object_ads_key"
