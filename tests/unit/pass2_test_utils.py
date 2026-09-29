"""Shared markers and a verification-pass driver for pass-2 tests; import after bootstrap."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ad_reviewer import ReviewResult, ReviewVerdict
from ad_validator import AdValidator
from audio_processor import AudioProcessor, get_replacement_duration
from main_app import processing
from utils.time import adjust_timestamp

NO_SPLICE = 'no_splice_evidence'


def _hold(start, end, reason=NO_SPLICE):
    return {'start': start, 'end': end, 'held_for_review': True,
            'was_cut': False, 'hold_reason': reason, 'sponsor': 'Acme'}


def _ad(start, end, confidence=0.95, category='sponsor', **extra):
    return {'start': start, 'end': end, 'confidence': confidence,
            'sponsor': 'Acme', 'reason': 'Acme sponsor read', 'category': category,
            'validation': {'decision': 'ACCEPT', 'adjusted_confidence': confidence},
            **extra}


def _pair(start, end, confidence=0.95, category='sponsor', cuts=(), **extra):
    beep = get_replacement_duration()
    orig = _ad(start, end, confidence, category, **extra)
    proc = dict(orig, start=adjust_timestamp(start, list(cuts), beep),
                end=adjust_timestamp(end, list(cuts), beep))
    return proc, orig


def _ctx():
    return SimpleNamespace(
        slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1,
        podcast_name='Example Podcast', episode_title='Episode',
        podcast_description='', episode_description='')


def _spans(ads):
    return [(round(a['start'], 2), round(a['end'], 2)) for a in ads]


def _user_corrections(slug, episode_id):
    """The (fp, confirmed) corrections the test's db holds."""
    return processing._load_user_corrections(slug, episode_id, processing.db)


def _approval_db(monkeypatch, confirmed=None):
    """Stub the db and storage that _file_corroborated_hold_approvals reads; returns the db."""
    db = MagicMock()
    db.get_false_positive_corrections.return_value = []
    db.get_confirmed_corrections.return_value = list(confirmed or [])
    db.get_original_segments.return_value = [{'start': 0.0, 'end': 30.0}]
    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', MagicMock())
    return db


def _verdict(kind, start, end, adjusted=None, reasoning='Sponsor read for Acme', **kwargs):
    return ReviewVerdict(
        pool='accepted', pass_num=2, verdict=kind,
        original_start=start, original_end=end,
        adjusted_start=adjusted[0] if adjusted else None,
        adjusted_end=adjusted[1] if adjusted else None,
        reasoning=reasoning, confidence=0.9, model_used='test-model', **kwargs)


def _release_confirm(hold_span, span, reason=NO_SPLICE, hold_id=None):
    """An auto-filed release confirm; reason=None leaves hold_reason unset."""
    return {'start': hold_span[0], 'end': hold_span[1], 'correction_type': 'confirm',
            'auto_filed': True, 'confirmed_span': {'start': span[0], 'end': span[1]},
            **({'hold_reason': reason} if reason else {}),
            **({'hold_id': hold_id} if hold_id else {})}


def _recut_validate(markers, confirms):
    validator = AdValidator(episode_duration=3000.0, segments=[],
                            confirmed_corrections=confirms, min_cut_confidence=0.8)
    return validator.validate(markers).ads


def gate_passthrough(processed, original, *args, **kwargs):
    """Stand-in for the pass-2 confidence gate that cuts every finding."""
    return list(processed), list(original), [], 0, []


def drive_verification_pass(findings=(), *, pairs=None, holds=(), cuts=(), kept=(), trims=(),
                            fp=(), duration=3000.0, validate=None, gate=None,
                            pass2_reviewer=None, hold_verdicts=None, status=None,
                            recut_error=None, audio=None, segments=None,
                            segment_actions=None, extra_patches=(), cue_gate_enabled=False,
                            pass1_reviewer_rejects=(), **pass_kwargs):
    """Run _run_verification_pass over findings with detection, validation and reviewers stubbed.

    validate, gate and pass2_reviewer stand in for the processing helpers (default: pass
    through, real gate); hold_verdicts turns the hold-release review on with those verdicts.
    """
    processor = AudioProcessor()
    rendered = {}

    def render(path, segs, cut_barriers=None, hard_barriers=None):
        if recut_error:
            raise recut_error
        rendered.update(requested=[dict(s) for s in segs], cut_barriers=cut_barriers,
                        hard_barriers=hard_barriers)
        return '/tmp/pass2-recut.mp3', processor.compute_applied_cuts(
            segs, duration, cut_barriers, hard_barriers=hard_barriers)

    if audio is None:
        audio = MagicMock()
        audio.get_audio_duration.return_value = duration
        audio.process_episode.side_effect = render
    fake_db = MagicMock()
    floors = {'verification_miss_hold_min_confidence': 0.6,
              'verification_miss_autocut_min_confidence': 0.0}
    fake_db.get_setting_float.side_effect = lambda key, default=None: floors.get(key, default)
    fake_db.get_setting.return_value = 'false'
    validated = []

    def run_validate(*args, **kwargs):
        validated.append({'orig': _spans(args[3]), 'kwargs': kwargs})
        if validate:
            return validate(*args, **kwargs)
        return list(args[2]), list(args[3])

    hold_reviews = []

    def hold_review(**kwargs):
        hold_reviews.append(_spans(kwargs['accepted_ads']))
        return ReviewResult(verdicts=list(hold_verdicts or []))

    if pairs is None:
        pairs = [_pair(*f, cuts=cuts) for f in findings]
    cut_list = [dict(c) for c in cuts]
    run_stats = {}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        p(processing, 'db', fake_db)
        p(processing, 'storage')
        verifier_cls = stack.enter_context(patch('verification_pass.VerificationPass'))
        p(processing, '_apply_pass2_heuristic_rolls')
        p(processing, '_validate_verification_ads', side_effect=run_validate)
        if gate:
            p(processing, '_gate_verification_ads_by_confidence', side_effect=gate)
        p(processing, '_apply_pass2_reviewer',
          side_effect=pass2_reviewer or (lambda *a, **k: None))
        p(processing, '_ad_review_enabled', lambda db: hold_verdicts is not None)
        p(processing, '_build_reviewer', lambda db, det: SimpleNamespace(review=hold_review))
        p(processing.ad_detector, 'get_verification_model', lambda: 'test-model')
        p(processing.ad_detector, 'get_verification_provider', lambda: None)
        for extra in extra_patches:
            stack.enter_context(extra)
        verifier_cls.return_value.verify.return_value = {
            'ads': [o for _p, o in pairs], 'ads_processed': [p_ for p_, _o in pairs],
            'segments': segments or [
                {'start': 0.0, 'end': duration, 'text': 'Acme sponsor read'}],
            'status': status,
        }
        kwargs = dict(
            original_segments=[], pass1_held_markers=list(holds),
            pass1_kept_markers=list(kept), pass1_trim_ranges=list(trims),
            pass1_reviewer_rejects=list(pass1_reviewer_rejects),
            segment_actions=segment_actions or {'sponsor': 'remove', 'self_promo': 'keep'},
            false_positive_corrections=list(fp), cue_gate_enabled=cue_gate_enabled,
            run_stats=run_stats)
        kwargs.update(pass_kwargs)
        output = processing._run_verification_pass(
            _ctx(), '/tmp/pass1-output.mp3', cut_list, False, 0.8, audio, None, **kwargs)
    return SimpleNamespace(output=output, rendered=rendered, validated=validated,
                           hold_reviews=hold_reviews, stats=run_stats, audio=audio,
                           cuts=cut_list)
