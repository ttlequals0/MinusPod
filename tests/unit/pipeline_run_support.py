"""Shared pipeline driver for the processing-mode and LLM-optional tests."""
import inspect
import os
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pipeline_run_support_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

import llm_client  # noqa: E402
import llm_route  # noqa: E402
import main_app.processing as processing  # noqa: E402
from main_app.pipeline import common, transcribe, signals, cuts, review, publish, failure  # noqa: E402
from main_app.pipeline import recut as recut_stage  # noqa: E402
from main_app.pipeline import finalize as finalize_stage  # noqa: E402
from main_app.pipeline import verify as verify_stage  # noqa: E402
from main_app.pipeline import detect as detect_stage  # noqa: E402
from main_app.pipeline import admission as admission_stage  # noqa: E402
import run_context  # noqa: E402

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]
_PROBE_PHASES_UNSET = object()


def _all_client_classes(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_client_classes(sub)


_FACTORIES = ('get_llm_client', 'get_client_for_provider', 'create_client_for_provider',
              '_build_client', 'client_for_route')
_REQUESTS = ('messages_create', 'list_models', 'verify_connection', 'probe_once',
             'probe_json_format_support', 'probe_json_schema_support')


class RecordedCalls(list):
    """Client factory and request calls that went through; refused holds (factory, caller) pairs the provider none refused."""

    def __init__(self):
        super().__init__()
        self.refused = []


# Callers allowed to ask the factory under none: they treat the refusal as "no client".
REFUSED_CALLER_ALLOWLIST = frozenset({'initialize_client'})


def _install_recorder(monkeypatch):
    """Record client factory and request calls made through the client classes; other HTTP is not recorded."""
    calls = RecordedCalls()

    def recorder(name):
        def record(*args, **kwargs):
            if name in _FACTORIES and llm_client.llm_disabled():
                calls.refused.append((name, inspect.stack()[1].function))
                raise llm_client.LLMDisabledError('The LLM provider is set to none; no client is available')
            calls.append(name)
            raise AssertionError(f'model call attempted: {name}')
        return record

    originals = {getattr(owner, name) for owner in (llm_client, llm_route)
                 for name in _FACTORIES if hasattr(owner, name)}
    for module in list(sys.modules.values()):
        for name in _FACTORIES:
            if getattr(module, name, None) in originals:
                monkeypatch.setattr(module, name, recorder(name))
    for cls in (llm_client.LLMClient, *_all_client_classes(llm_client.LLMClient)):
        for name in _REQUESTS:
            if name in vars(cls):
                monkeypatch.setattr(cls, name, recorder(f'{cls.__name__}.{name}'))
    monkeypatch.setattr(llm_client, '_client_cache', {})
    return calls


_INACTIVE_FAILOVER_STATE = {'active': False, 'source': None, 'since': None, 'reason': None}


def _run_pipeline(podcast_row, cue_template_counts=None, cue_templates=None,
                  enable_ad_review=False, admission=None,
                  download_error=None, detect_error=None,
                  token_cost=0.012, real_token_tracking=False,
                  approval_recut=False, route_snapshot=None,
                  failover_state=None, standby_dispatch=False,
                  probe_phases=_PROBE_PHASES_UNSET, episode_row=None,
                  real_detect=False, refine_cuts_all=False, real_stages=(),
                  manual_approval=None, settings=None, differential=None):
    """Run stubbed pipeline stages with optional frozen routes and failover state.

    real_stages names stages left real: refine, reviewer, verify, assets, finalize.
    """
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        slot_targets = {'primary': 'llm:primary', 'secondary': 'llm:secondary',
                        'failover': 'llm:failover'}
        probe_selector = p(
            processing.failover, 'run_probe_targets',
            side_effect=lambda phases, whisper_required: list(dict.fromkeys(
                slot_targets[route.get('credential_slot', 'primary')]
                for route in (phases or {}).values() if isinstance(route, dict)
            )),
        )
        ensure_probes = p(processing.failover, 'ensure_fresh_probes')
        if probe_phases is not _PROBE_PHASES_UNSET:
            p(admission_stage, '_active_phases_for_admission', return_value=probe_phases)
        if route_snapshot is not None:
            p(admission_stage, '_resolve_or_load_route_snapshot', return_value=route_snapshot)
            p(admission_stage, '_assert_route_snapshot_current')
        if failover_state is not None:
            p(processing.failover, 'state',
              side_effect=lambda t: failover_state.get(t, _INACTIVE_FAILOVER_STATE))
            p(processing.failover, 'is_active',
              side_effect=lambda t: failover_state.get(t, _INACTIVE_FAILOVER_STATE)['active'])
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing.ad_detector, 'get_model', return_value='test-model')
        p(processing.ad_detector, 'get_verification_model', return_value='test-model')
        if not real_token_tracking:
            p(processing, 'start_episode_token_tracking')
            p(finalize_stage, 'get_episode_token_totals', return_value={
                'input_tokens': 1, 'output_tokens': 1, 'cost': token_cost,
            })
            p(failure, 'get_episode_token_totals', return_value={
                'input_tokens': 1, 'output_tokens': 1, 'cost': token_cost,
            })
        p(processing, 'get_available_memory_gb', return_value=None)
        p(finalize_stage, 'get_available_memory_gb', return_value=None)
        p(common, 'get_min_cut_confidence', return_value=0.8)
        dat = p(transcribe, '_download_and_transcribe',
                return_value=('/tmp/mode.mp3', SEGMENTS))
        diff_fetch = p(signals, '_run_differential_fetch', return_value=differential)
        analyze = p(signals, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        if real_token_tracking:
            def detect_with_usage(*args, **kwargs):
                run_context.current().tokens.add(120, 30, token_cost)
                return [], 0, None

            detect = p(detect_stage, '_detect_ads_first_pass',
                       side_effect=detect_with_usage)
        elif real_detect:
            detect = p(detect_stage, '_detect_ads_first_pass',
                       side_effect=detect_stage._detect_ads_first_pass)
        else:
            detect = p(detect_stage, '_detect_ads_first_pass',
                       return_value=([], 0, None))
        if refine_cuts_all:
            def cut_everything(slug, episode_id, ads, *a, **k):
                marked = [dict(ad, was_cut=True) for ad in ads]
                return marked, marked
            refine = p(detect_stage, '_refine_and_validate', side_effect=cut_everything)
        elif 'refine' in real_stages:
            refine = p(detect_stage, '_refine_and_validate',
                       side_effect=detect_stage._refine_and_validate)
        else:
            refine = p(detect_stage, '_refine_and_validate', return_value=([], []))
        if 'reviewer' in real_stages:
            reviewer = p(review, '_run_ad_reviewer', side_effect=review._run_ad_reviewer)
        else:
            reviewer = p(review, '_run_ad_reviewer', return_value=([], []))
        sweep_result = (lambda slug, episode_id, ads, *a, **k: ads
                        ) if 'refine' in real_stages else None
        p(cuts, '_snap_terminal_starts', return_value=[], side_effect=sweep_result)
        p(cuts, '_complete_cut_tails', return_value=[], side_effect=sweep_result)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(recut_stage, 'AudioProcessor', new=local_ap_cls)
        if 'verify' in real_stages:
            verify = p(verify_stage, '_run_verification_pass',
                       side_effect=verify_stage._run_verification_pass)
        else:
            verify = p(verify_stage, '_run_verification_pass',
                       return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
        if 'assets' in real_stages:
            p(publish, '_generate_assets', side_effect=publish._generate_assets)
        else:
            p(publish, '_generate_assets')
        if 'finalize' in real_stages:
            finalize = p(finalize_stage, '_finalize_episode',
                         side_effect=finalize_stage._finalize_episode)
        elif real_token_tracking:
            finalize = p(finalize_stage, '_finalize_episode',
                         side_effect=lambda *args, **kwargs:
                         finalize_stage.get_episode_token_totals())
        else:
            finalize = p(finalize_stage, '_finalize_episode')
        recut = None
        if approval_recut:
            p(verify_stage, '_file_corroborated_hold_approvals', return_value=1)
            recut = p(recut_stage, '_recut_episode', side_effect=lambda *args, **kwargs:
                      (finalize_stage.get_episode_token_totals(), True)[1])
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        path_exists = processing.os.path.exists
        p(processing.os.path, 'exists', side_effect=lambda path:
          False if path in ('/tmp/mode.mp3', '/tmp/cut.mp3') else path_exists(path))

        db.get_episode.return_value = episode_row or {}
        if manual_approval is not None:
            db.requires_manual_approval.return_value = manual_approval
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_podcast_row.return_value = podcast_row
        db.reserve_provider_spend.return_value = (
            admission or {'allowed': True, 'reservation_id': 'provider-run-1'})
        # Reconcile now reads the ledger sum, not the in-process accumulator
        # this harness fakes via ctx.tokens.add above.
        db.get_run_provider_spend.return_value = round(token_cost * 1_000_000)
        db.run_provider_spend_is_incomplete.return_value = False
        if download_error is not None:
            dat.side_effect = download_error
        if detect_error is not None:
            detect.side_effect = detect_error
        if enable_ad_review:
            db.get_setting.side_effect = (
                lambda key, *a, **k: 'true' if key == 'enable_ad_review' else 'false')
        else:
            db.get_setting.return_value = 'false'
        if settings:
            base_get_setting = db.get_setting.side_effect or (
                lambda key, *a, **k: db.get_setting.return_value)
            db.get_setting.side_effect = (
                lambda key, *a, **k: settings[key] if key in settings
                else base_get_setting(key, *a, **k))
        db.get_setting_bool.return_value = False
        db.get_all_settings.return_value = {}
        db.cue_template_paired_episode_counts.return_value = cue_template_counts or {}
        db.list_cue_templates_for_feed_ui.return_value = cue_templates or []
        db.cue_template_recent_activity.return_value = []
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        ctx = run_context.begin('mode-feed', 'ep1') if standby_dispatch else None
        if standby_dispatch:
            def dispatched(*args, **kwargs):
                ctx.note_llm_failover('detection', 1)
                return [], 0, None
            detect.side_effect = dispatched
        try:
            result = processing.process_episode(
                'mode-feed', 'ep1', 'https://example.com/ep1.mp3')
        finally:
            if ctx:
                run_context.end(ctx)
    return {'result': result, 'detect': detect, 'verify': verify,
            'analyze': analyze, 'refine': refine, 'finalize': finalize,
            'dat': dat, 'diff_fetch': diff_fetch, 'db': db, 'reviewer': reviewer, 'recut': recut,
            'probe_selector': probe_selector, 'ensure_probes': ensure_probes}


def _row(pt=None, skip=None, mode=None):
    return {'id': 1, 'slug': 'mode-feed', 'description': None,
            'tags': None, 'dai_platform': None,
            'passthrough_enabled': pt, 'skip_ad_detection': skip,
            'detection_mode': mode}
