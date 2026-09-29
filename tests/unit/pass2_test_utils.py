"""Shared markers and a verification-pass driver for pass-2 tests; import after bootstrap."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ad_reviewer import ReviewResult
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


def drive_verification_pass(findings, *, holds=(), cuts=(), kept=(), trims=(), fp=(),
                            duration=3000.0, validate=None, pass2_reviewer=None,
                            hold_verdicts=None, status=None, recut_error=None,
                            cue_gate_enabled=False, pass1_reviewer_rejects=()):
    """Run _run_verification_pass over findings with detection, validation and reviewers stubbed.

    validate and pass2_reviewer stand in for the processing helpers (default: pass through);
    hold_verdicts turns the hold-release review on with those verdicts.
    """
    audio = MagicMock()
    audio.get_audio_duration.return_value = duration
    processor = AudioProcessor()
    rendered = {}

    def render(path, segs, cut_barriers=None, hard_barriers=None):
        if recut_error:
            raise recut_error
        rendered.update(requested=[dict(s) for s in segs], cut_barriers=cut_barriers,
                        hard_barriers=hard_barriers)
        return '/tmp/pass2-recut.mp3', processor.compute_applied_cuts(
            segs, duration, cut_barriers, hard_barriers=hard_barriers)

    audio.process_episode.side_effect = render
    fake_db = MagicMock()
    floors = {'verification_miss_hold_min_confidence': 0.6,
              'verification_miss_autocut_min_confidence': 0.0}
    fake_db.get_setting_float.side_effect = lambda key, default=None: floors.get(key, default)
    fake_db.get_false_positive_corrections.return_value = list(fp)
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

    pairs = [_pair(*f, cuts=cuts) for f in findings]
    run_stats = {}
    with patch.object(processing, 'db', fake_db), \
         patch.object(processing, 'storage'), \
         patch('verification_pass.VerificationPass') as verifier_cls, \
         patch.object(processing, '_apply_pass2_heuristic_rolls'), \
         patch.object(processing, '_validate_verification_ads', side_effect=run_validate), \
         patch.object(processing, '_apply_pass2_reviewer',
                      side_effect=pass2_reviewer or (lambda *a, **k: None)), \
         patch.object(processing, '_ad_review_enabled',
                      lambda db: hold_verdicts is not None), \
         patch.object(processing, '_build_reviewer',
                      lambda db, det: SimpleNamespace(review=hold_review)), \
         patch.object(processing.ad_detector, 'get_verification_model',
                      lambda: 'test-model', create=True), \
         patch.object(processing.ad_detector, 'get_verification_provider',
                      lambda: None, create=True):
        verifier_cls.return_value.verify.return_value = {
            'ads': [o for _p, o in pairs], 'ads_processed': [p for p, _o in pairs],
            'segments': [{'start': 0.0, 'end': duration, 'text': 'Acme sponsor read'}],
            'status': status,
        }
        output = processing._run_verification_pass(
            _ctx(), '/tmp/pass1-output.mp3', [dict(c) for c in cuts], False, 0.8,
            audio, None, original_segments=[], pass1_held_markers=list(holds),
            pass1_kept_markers=list(kept), pass1_trim_ranges=list(trims),
            pass1_reviewer_rejects=list(pass1_reviewer_rejects),
            segment_actions={'sponsor': 'remove', 'self_promo': 'keep'},
            false_positive_corrections=list(fp), cue_gate_enabled=cue_gate_enabled,
            run_stats=run_stats)
    return SimpleNamespace(output=output, rendered=rendered, validated=validated,
                           hold_reviews=hold_reviews, stats=run_stats)
