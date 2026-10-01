"""Splice-evidence consumers in AdValidator (spec 2.3a).

Builds on _audio_corroboration_source and the validate(..., audio_analysis=)
wiring, adding the splice_evidence source.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision
from utils.text import word_boundary_re
from tests.unit.marker_test_utils import ACME_REGISTRY, CLOSING


def _event(t, end=None, etype='digital_silence'):
    end = end if end is not None else t + 1.0
    return {'time': t, 'end_time': end, 'type': etype, 'depth_dbfs': -90.0,
            'duration_s': end - t, 'loudness_step_lu': None,
            'centroid_step_hz': None, 'flatness_step': None}


def _analysis(events, status='calibrated'):
    return {'splice_evidence': {'version': 1, 'events': events,
                                'calibration': {'status': status}}}


class TestSpliceCorroboration:
    def test_event_within_3s_of_start_returns_splice_evidence(self):
        validator = AdValidator(episode_duration=3600.0)
        validator.validate(
            [{'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'reason': 'x'}],
            audio_analysis=_analysis([_event(97.5)]))
        assert validator._audio_corroboration_source(
            {'start': 100.0, 'end': 130.0}) == 'splice_evidence'

    def test_event_beyond_3s_of_both_edges_returns_none(self):
        validator = AdValidator(episode_duration=3600.0)
        validator.validate(
            [{'start': 100.0, 'end': 130.0, 'confidence': 0.9, 'reason': 'x'}],
            audio_analysis=_analysis([_event(50.0)]))
        assert validator._audio_corroboration_source(
            {'start': 100.0, 'end': 130.0}) is None

    def test_vad_gap_clamp_bypassed_by_splice_event(self):
        # Untranscribed tail marker: without corroboration the vad_gap clamp
        # forces it below min_cut_confidence; a splice event at its start
        # bypasses the clamp (catch-22, spec 1.1 + 2.3a).
        ad = {'start': 3557.6, 'end': 3600.0, 'confidence': 0.85,
              'reason': 'untranscribed tail gap', 'detection_stage': 'vad_gap'}
        corroborated = AdValidator(episode_duration=3600.0,
                                   min_cut_confidence=0.80)
        result = corroborated.validate(
            [dict(ad)], audio_analysis=_analysis([_event(3556.9)]))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value
        assert result.ads[0]['corroborated_by'] == 'splice_evidence'

        bare = AdValidator(episode_duration=3600.0, min_cut_confidence=0.80)
        result = bare.validate([dict(ad)], audio_analysis=_analysis([]))
        assert result.ads[0]['validation']['decision'] == Decision.REVIEW.value
        assert 'corroborated_by' not in result.ads[0]


class TestNoSegmentsVadGapClamp:
    """Pins the no-segments branch of _verify_in_transcript. A fully
    untranscribed episode (segments=[]) with an uncorroborated vad_gap
    marker clamps confidence to min_cut_confidence - 0.01 so it routes to
    REVIEW; a splice event in range corroborates it and skips the clamp.
    The empty-segments branch used to return confidence unchanged; the clamp
    is deliberate, backstopped by the tail hold rule.
    """

    def _marker(self):
        return {'start': 100.0, 'end': 130.0, 'confidence': 0.90,
                'reason': 'untranscribed gap', 'detection_stage': 'vad_gap'}

    def test_no_segments_uncorroborated_vad_gap_is_clamped(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                min_cut_confidence=0.80)
        validator._audio_analysis = None
        ad = self._marker()
        result = validator._verify_in_transcript(ad, 0.90, [])
        assert result == pytest.approx(0.79)
        assert 'corroborated_by' not in ad

    def test_no_segments_splice_corroborated_vad_gap_not_clamped(self):
        validator = AdValidator(episode_duration=3600.0, segments=[],
                                min_cut_confidence=0.80)
        validator._audio_analysis = _analysis([_event(97.5)])
        ad = self._marker()
        result = validator._verify_in_transcript(ad, 0.90, [])
        assert result == pytest.approx(0.90)
        assert ad['corroborated_by'] == 'splice_evidence'


class TestSpliceVeto:
    """Zero-evidence veto (spec 2.3c): Vrbo-shaped 90s cut with no splice
    evidence anywhere near it is demoted to REVIEW + held_for_review."""

    _AD = {'start': 1800.0, 'end': 1890.0, 'confidence': 0.92,
           'reason': 'Vrbo vacation rental read with booking details',
           'detection_stage': 'claude'}

    def _validate(self, analysis, **kwargs):
        validator = AdValidator(episode_duration=3600.0, **kwargs)
        return validator.validate([dict(self._AD)], audio_analysis=analysis)

    def test_evidence_less_long_cut_demoted_to_review(self):
        result = self._validate(_analysis([]))
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['held_for_review'] is True
        assert ad['hold_reason'] == 'no_splice_evidence'

    def test_corroborated_cut_untouched(self):
        result = self._validate(_analysis([_event(1801.0)]))
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_event_near_edge_counts(self):
        result = self._validate(_analysis([_event(1892.0)]))  # 2s past end
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_cold_start_never_vetoes(self):
        result = self._validate(_analysis([], status='cold_start'))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_disabled_never_vetoes(self):
        result = self._validate(_analysis([]), splice_veto_enabled=False)
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_short_cut_not_vetoed(self):
        validator = AdValidator(episode_duration=3600.0)
        short = dict(self._AD, end=1855.0)  # 55s < 60s floor
        result = validator.validate([short], audio_analysis=_analysis([]))
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    @pytest.mark.parametrize('stage', ['manual', 'vad_gap', 'fingerprint',
                                       'dai_differential', 'cue'])
    def test_other_stages_not_vetoed(self, stage):
        """Only claude/text_pattern are subject to the splice veto; every
        other stage carries its own evidence and is exempt. vad_gap can still
        route to REVIEW via its own confidence clamp, but never with the
        no_splice_evidence hold, so assert the veto did not fire."""
        validator = AdValidator(episode_duration=3600.0)
        ad = dict(self._AD, detection_stage=stage)
        result = validator.validate([ad], audio_analysis=_analysis([]))
        result_ad = result.ads[0]
        assert result_ad.get('hold_reason') != 'no_splice_evidence'
        assert not result_ad.get('held_for_review')

    def test_no_audio_analysis_never_vetoes(self):
        validator = AdValidator(episode_duration=3600.0)
        result = validator.validate([dict(self._AD)])
        assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_dai_transition_pair_near_edge_exempts_from_veto(self):
        # Finding 1: Rule 3 must gate on full corroboration (not just splice events).
        # A long claude cut with a DAI transition pair near an edge is corroborated
        # and must not be vetoed, even when splice_evidence.events is empty.
        analysis = {
            'signals': [{
                'start': 1798.0, 'end': 1892.0,
                'signal_type': 'dai_transition_pair',
                'confidence': 0.95, 'duration': 94.0,
                'details': {'avg_delta_db': 14.0, 'start_direction': 'down',
                            'start_delta_db': 14.2, 'end_delta_db': 13.8,
                            'start_from_lufs': -16.0, 'start_to_lufs': -30.2,
                            'end_from_lufs': -30.0, 'end_to_lufs': -16.2},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_dai_differential_region_overlap_exempts_from_veto(self):
        # Finding 1: a dai_differential region overlapping the ad is corroboration
        # and must also exempt it from the splice veto.
        analysis = {
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
            'dai_differential': {'status': 'ok', 'regions': [
                {'start_s': 1790.0, 'end_s': 1900.0,
                 'kind': 'differential', 'corr': 0.0}
            ]},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_template_cue_near_edge_exempts_from_veto(self):
        # A learned ad-break template firing at an edge is splice evidence;
        # a long text_pattern cut bracketed by one must not be vetoed.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.99,
                'details': {'source': 'template', 'template_id': 1,
                            'cue_type': 'ad_break_boundary'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert 'held_for_review' not in ad

    def test_spectral_fallback_cue_does_not_exempt(self):
        # Spectral cues (no template source) stay advisory: too coarse to
        # count as splice evidence.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.9,
                'details': {'cue_type': 'ad_break_boundary'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'

    def test_non_ad_role_template_cue_does_not_exempt(self):
        # A show intro/outro stinger (role non_ad, #350) at an edge is not
        # ad-break evidence and must not lift the veto.
        analysis = {
            'signals': [{
                'start': 1889.5, 'end': 1891.0,
                'signal_type': 'audio_cue',
                'confidence': 0.99,
                'details': {'source': 'template', 'template_id': 2,
                            'role': 'non_ad', 'cue_type': 'show_outro'},
            }],
            'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}},
        }
        result = self._validate(analysis)
        ad = result.ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'


class TestSpliceVetoSponsorWaiver:
    """A 187 s cut on a calibrated feed with no audio corroboration."""

    def _run(self, text, stage='claude', registry=False, description=None,
             reason='Sponsor read'):
        segments = [{'start': 1000.0, 'end': 1187.0, 'text': text}]
        v = AdValidator(3600.0, segments, episode_description='',
                        sponsor_service=ACME_REGISTRY if registry else None)
        if description:
            v._description_sponsor_re = word_boundary_re((description,))
        # A category keeps these on the splice veto, not the transcript-evidence hold (#807).
        ad = {'start': 1000.0, 'end': 1187.0, 'confidence': 0.95, 'category': 'sponsor',
              'reason': reason, 'detection_stage': stage}
        return v.validate([ad], audio_analysis=_analysis([])).ads[0]

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_registry_confirmed_is_accepted(self, stage, caplog):
        with caplog.at_level('INFO'):
            ad = self._run(CLOSING, stage=stage, registry=True)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')
        assert ('INFO: Splice veto waived, sponsor confirmed by registry'
                in ad['validation']['flags'])
        assert 'Splice veto waived for 1000.0s-1187.0s: sponsor confirmed by registry' \
            in caplog.text
        assert caplog.text.count('treating as confirmed') == 1

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_transcript_confirmed_is_accepted(self, stage):
        ad = self._run(CLOSING, stage=stage, description='Acme')
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
                in ad['validation']['flags'])

    VANITY_READ = ('Acme is one scoop that covers your daily nutrients. I take Acme every '
                   "morning. Go to drinkacme {tld} slash show. That's drinkacme {tld} slash show.")

    @pytest.mark.parametrize('tld', ['.com', ' .com', ' dot com'])
    def test_call_to_action_link_on_another_domain_is_registry_confirmed(self, tld):
        ad = self._run(self.VANITY_READ.format(tld=tld), registry=True)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ('INFO: Splice veto waived, sponsor confirmed by registry'
                in ad['validation']['flags'])

    def test_call_to_action_link_confirms_a_description_sponsor(self):
        ad = self._run(self.VANITY_READ.format(tld=' .com'), description='Acme')
        assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
                in ad['validation']['flags'])

    def test_bare_link_on_another_domain_is_held(self):
        text = self.VANITY_READ.format(tld=' .com').replace('Go to', 'Just')
        ad = self._run(text, registry=True)
        assert ad['hold_reason'] == 'no_splice_evidence'

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_reason_only_is_held(self, stage):
        ad = self._run('ordinary conversation about the week', stage=stage,
                       description='Acme', reason='Acme sponsor read')
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_unconfirmed_is_held(self, stage):
        ad = self._run('I use Acme at home, Acme is fine', stage=stage,
                       registry=True)
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'


def _region(start, end, kind='identical', corr=1.0):
    return {'start_s': start, 'end_s': end, 'kind': kind, 'corr': corr}


BAKED_IN_FLAG = 'INFO: Splice veto skipped, cross-fetch shows baked-in audio'


class TestSpliceVetoCrossFetchWaiver:
    """A 71.8 s claude cut on a calibrated feed with no splice evidence."""

    START, END = 609.0, 680.8

    def _run(self, dai_differential, text='See what a trace looks like at acme.com slash show',
             registry=False):
        segments = [{'start': self.START, 'end': self.END, 'text': text}]
        v = AdValidator(3600.0, segments, episode_description='',
                        sponsor_service=ACME_REGISTRY if registry else None)
        ad = {'start': self.START, 'end': self.END, 'confidence': 0.93,
              'reason': 'See what a trace looks like at acme.com slash show',
              'detection_stage': 'claude'}
        analysis = _analysis([])
        if dai_differential is not None:
            analysis['dai_differential'] = dai_differential
        return v.validate([ad], audio_analysis=analysis).ads[0]

    def _assert_held(self, ad):
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'
        assert BAKED_IN_FLAG not in ad['validation']['flags']

    def test_whole_episode_identical_is_accepted(self, caplog):
        payload = {'status': 'no_differential', 'regions': [_region(0.0, 3600.0)]}
        with caplog.at_level('INFO'):
            ad = self._run(payload)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')
        assert BAKED_IN_FLAG in ad['validation']['flags']
        assert 'Splice veto skipped for 609.0s-680.8s' in caplog.text
        assert '(100% identical)' in caplog.text

    def test_unknown_seam_slivers_do_not_break_the_rule(self):
        payload = {'status': 'no_differential', 'regions': [
            _region(0.0, 630.0), _region(630.0, 631.5, 'unknown', None),
            _region(631.5, 660.0), _region(660.0, 661.5, 'unknown', None),
            _region(661.5, 3600.0)]}
        ad = self._run(payload)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert BAKED_IN_FLAG in ad['validation']['flags']

    def test_ok_status_with_identical_span_is_held(self):
        payload = {'status': 'ok', 'regions': [
            _region(0.0, 1200.0), _region(1200.0, 1260.0, 'differential', 0.1),
            _region(1260.0, 3600.0)]}
        self._assert_held(self._run(payload))

    def test_differential_region_overlapping_the_span_is_held(self):
        # 2 s leaves the rest 97% identical, so only the overlap rule holds; corr 0.7 does not corroborate.
        payload = {'status': 'no_differential', 'regions': [
            _region(0.0, 640.0), _region(640.0, 642.0, 'differential', 0.7),
            _region(642.0, 3600.0)]}
        self._assert_held(self._run(payload))

    def test_ninety_percent_identical_coverage_is_held(self):
        cut = self.START + 0.9 * (self.END - self.START)
        payload = {'status': 'no_differential', 'regions': [
            _region(0.0, cut), _region(cut, 3600.0, 'unknown', None)]}
        self._assert_held(self._run(payload))

    @pytest.mark.parametrize('payload', [
        None,
        {'status': 'unreliable_reencode', 'regions': []},
        {'status': 'error', 'regions': [], 'error': 'boom'},
        {'status': 'no_differential', 'regions': [_region(0.0, 3600.0, corr=0.0)]},
    ])
    def test_fallbacks_keep_todays_hold(self, payload):
        self._assert_held(self._run(payload))

    def test_sponsor_waiver_keeps_precedence(self):
        payload = {'status': 'no_differential', 'regions': [_region(0.0, 3600.0)]}
        ad = self._run(payload, text=CLOSING, registry=True)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ('INFO: Splice veto waived, sponsor confirmed by registry'
                in ad['validation']['flags'])
        assert BAKED_IN_FLAG not in ad['validation']['flags']


class TestSpliceVetoNamedSponsor:
    """The detector's own sponsor, unknown to registry and description, spoken in a long read."""

    START, END = 609.0, 680.8
    VANITY = 'See what a trace looks like at acme.com slash show'

    FRAMED = 'This episode is brought to you by Acme. See how it works at acme.com slash show.'

    def _run(self, text, sponsor=None, reason='Ad brought to you by Acme', end=None,
             confidence=0.93, podcast_name=None):
        end = end or self.END
        texts = [text] if isinstance(text, str) else text
        step = (end - self.START) / len(texts)
        segments = [{'start': self.START + i * step, 'end': self.START + (i + 1) * step,
                     'text': t} for i, t in enumerate(texts)]
        v = AdValidator(3600.0, segments, episode_description='', sponsor_service=None,
                        podcast_name=podcast_name)
        ad = {'start': self.START, 'end': end, 'confidence': confidence, 'reason': reason,
              'detection_stage': 'claude', 'category': 'sponsor'}
        if sponsor is not None:
            ad['sponsor'] = sponsor
        return v.validate([ad], audio_analysis=_analysis([])).ads[0]

    def _assert_held(self, ad):
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'
        assert ad['validation']['sponsor_confirmed'] is False

    def test_reason_brand_with_spoken_framing_and_vanity_link_is_accepted(self):
        ad = self._run(self.FRAMED)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')
        assert ad['validation']['sponsor_confirmed'] is True
        assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
                in ad['validation']['flags'])

    def test_reason_brand_with_only_a_link_is_held(self):
        self._assert_held(self._run(self.VANITY))

    def test_reason_brand_with_only_spoken_framing_is_held(self):
        self._assert_held(self._run('This episode is brought to you by Acme. More on that later.'))

    def test_production_reason_framing_names_the_sponsor(self):
        reason = ('Based on transcript: This episode of the show is brought to you by Acme. '
                  'A-C-M-E. This is why you need Acme.')
        ad = self._run(self.FRAMED, reason=reason)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['sponsor_confirmed'] is True
        assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
                in ad['validation']['flags'])

    def test_structured_sponsor_with_vanity_link_is_accepted(self):
        ad = self._run(self.VANITY, sponsor='Acme', reason='Host read')
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_brand_only_in_reason_is_held(self):
        self._assert_held(self._run('ordinary conversation about the week'))

    def test_bare_domain_mention_is_held(self):
        self._assert_held(self._run('I tried acme.com last week', sponsor='Acme'))

    def test_generic_word_spoken_conversationally_is_held(self):
        self._assert_held(self._run('Indeed, that was a wild week. Indeed it was.',
                                    sponsor='Indeed', reason='Indeed sponsor read'))

    @pytest.mark.parametrize('sponsor', ['Website', 'Podcast'])
    def test_generic_label_with_path_link_is_held(self, sponsor):
        text = f'Find the {sponsor.lower()} at {sponsor.lower()}.com slash show'
        self._assert_held(self._run(text, sponsor=sponsor, reason='Host read'))

    def test_second_listed_sponsor_with_an_offer_confirms(self):
        text = 'Acme came up earlier. Initech: use code SHOW for 20% off at Initech.'
        ad = self._run(text, sponsor='Acme, Initech', reason='Host read')
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['sponsor_confirmed'] is True

    def test_listener_support_thanks_is_held(self):
        text = 'Thanks to our patrons for supporting the show. Join at acme.com slash support.'
        self._assert_held(self._run(text, reason='Patrons sponsor read: thanks to our patrons '
                                                 'for supporting the show'))

    def test_listener_named_framing_is_held(self):
        text = 'This show is brought to you by Listeners Like You. Visit listenerslikeyou.com.'
        self._assert_held(self._run(text, reason='Ad brought to you by Listeners Like You'))

    def test_thanked_person_without_link_or_offer_is_held(self):
        text = 'Thanks to Steve for supporting the show, he has been great.'
        self._assert_held(self._run(text, reason='Sponsor thanks: thanks to Steve for supporting'))

    def test_show_own_name_with_patreon_link_is_held(self):
        text = ('This show is brought to you by Example Cast listeners. Join at '
                'examplecast.com slash patreon and use code CAST for 10 percent off.')
        self._assert_held(self._run(text, reason='Ad brought to you by Example Cast',
                                    podcast_name='Example Cast'))

    def test_reason_brand_with_framing_and_call_to_action_link_confirms(self):
        text = 'This episode is brought to you by Acme. Go to drinkacme .com slash show.'
        ad = self._run(text)
        assert ad['validation']['sponsor_confirmed'] is True

    def test_reason_brand_with_call_to_action_link_but_no_framing_is_held(self):
        self._assert_held(self._run('I take Acme every morning. Go to drinkacme .com slash show.'))

    def test_content_link_with_framing_only_in_reason_is_held(self):
        self._assert_held(self._run('See github.com slash owner slash repo for the code.',
                                    reason='Sponsored by GitHub'))

    def test_content_link_with_spoken_framing_confirms(self):
        text = 'This segment is sponsored by GitHub, github.com slash owner.'
        ad = self._run(text, reason='Sponsored by GitHub')
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['sponsor_confirmed'] is True

    def test_long_content_span_naming_a_site_stays_held(self):
        text = ('We walked through the code together, it is at github.com slash owner slash '
                'repo, and then we talked about the week.')
        ad = self._run(text, reason='GitHub sponsor read, see github.com',
                       end=self.START + 360.0, confidence=0.85)
        assert ad['held_for_review'] is True
        assert ad['validation']['sponsor_confirmed'] is False

    def test_long_read_judged_against_confirmed_ceiling(self):
        ad = self._run(self.FRAMED, end=self.START + 480.0)
        assert not any('Very long' in f for f in ad['validation']['flags'])
        assert 'INFO: Long (480.0s) but sponsor confirmed' in ad['validation']['flags']


    def test_offer_far_from_the_framing_is_held(self):
        texts = ['The festival was sponsored by Acme this year.', 'We saw three bands.',
                 'Tickets are 20% off at the door.']
        self._assert_held(self._run(texts, reason='Sponsored by Acme'))

    def test_offer_in_the_segment_after_the_framing_confirms(self):
        texts = ['The festival was sponsored by Acme this year.',
                 'Tickets are 20% off at the door.', 'We saw three bands.']
        ad = self._run(texts, reason='Sponsored by Acme')
        assert ad['validation']['sponsor_confirmed'] is True

    def test_vanity_link_anywhere_in_the_span_confirms(self):
        texts = ['This episode is brought to you by Acme.', 'We saw three bands.',
                 'See how it works at acme.com slash show.']
        ad = self._run(texts)
        assert ad['validation']['sponsor_confirmed'] is True

    def test_brand_word_inside_the_show_title_stays_a_candidate(self):
        text = ('This episode is brought to you by Acme. Go to acme.com slash weekly '
                'and use code WEEKLY for 20% off.')
        ad = self._run(text, podcast_name='Acme Weekly')
        assert ad['validation']['sponsor_confirmed'] is True

    @pytest.mark.parametrize('name,show,excluded', [
        ('The Daily Tech News', 'The Daily Tech News Show', True),
        ('Example Cast', 'Example Cast', True),
        ('Example Cast Listeners', 'Example Cast', True),
        ('Acme', 'Acme Weekly', False),
        ('Daily Tech', 'The Daily Tech News Show', False),
    ])
    def test_show_name_exclusion(self, name, show, excluded):
        v = AdValidator(3600.0, [], episode_description='', sponsor_service=None,
                        podcast_name=show)
        assert v._is_audience_or_show(name) is excluded


class TestAudioCorroborationRecord:
    """Every eligible long transcript-detected cut records its audio evidence, held or cut."""

    _AD = TestSpliceVeto._AD

    def _validate(self, analysis, ad=None, **kwargs):
        validator = AdValidator(episode_duration=3600.0, **kwargs)
        return validator.validate([dict(ad or self._AD)], audio_analysis=analysis).ads[0]

    def test_held_marker_records_none(self):
        ad = self._validate(_analysis([]))
        assert ad['hold_reason'] == 'no_splice_evidence'
        assert ad['validation']['audio_corroboration'] == 'none'

    def test_cut_marker_records_its_source(self):
        ad = self._validate(_analysis([_event(1801.0)]))
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['audio_corroboration'] == 'splice_evidence'

    @pytest.mark.parametrize('kwargs,status', [
        ({'splice_veto_enabled': False}, 'calibrated'), ({}, 'cold_start'), ({}, 'host_read')])
    def test_recorded_whether_or_not_the_veto_runs(self, kwargs, status):
        ad = self._validate(_analysis([], status=status), **kwargs)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ad['validation']['audio_corroboration'] == 'none'

    @pytest.mark.parametrize('ad', [
        dict(TestSpliceVeto._AD, end=1855.0),
        dict(TestSpliceVeto._AD, detection_stage='vad_gap'),
        dict(TestSpliceVeto._AD, confidence=0.3),
    ])
    def test_absent_on_ineligible_markers(self, ad):
        assert 'audio_corroboration' not in self._validate(_analysis([]), ad=ad)['validation']

    def test_absent_without_splice_evidence(self):
        validator = AdValidator(episode_duration=3600.0)
        ad = validator.validate([dict(self._AD)]).ads[0]
        assert 'audio_corroboration' not in ad['validation']


class TestProtectedBoundsClamp:
    def test_merged_protected_end_clamped_to_duration(self):
        # A protected member recorded past EOF must not survive validation,
        # or the reviewer floor re-expands the marker beyond the file.
        validator = AdValidator(episode_duration=2315.9)
        ad = {'start': 2159.6, 'end': 2346.6, 'confidence': 0.9,
              'reason': 'tail pattern', 'detection_stage': 'text_pattern',
              'merged_distinct_ads': True,
              'merged_protected_start': 2159.6,
              'merged_protected_end': 2346.6}
        result = validator.validate([ad])
        out = result.ads[0]
        assert out['end'] == 2315.9
        assert out['merged_protected_end'] == 2315.9

    def test_merged_member_spans_clamped_to_episode_bounds(self):
        # The reviewer reads the member list, so a member left past EOF would
        # fail its coverage check on every later proposal.
        validator = AdValidator(episode_duration=2315.9)
        ad = {'start': 2159.6, 'end': 2346.6, 'confidence': 0.9,
              'reason': 'tail pattern', 'detection_stage': 'text_pattern',
              'merged_distinct_ads': True,
              'merged_protected_start': 2159.6,
              'merged_protected_end': 2346.6,
              'merged_member_spans': [
                  {'start': 2159.6, 'end': 2346.6, 'stage': 'fingerprint'},
                  {'start': 2340.0, 'end': 2346.6, 'stage': 'claude'}]}
        result = validator.validate([ad])
        out = result.ads[0]
        assert out['merged_member_spans'] == [
            {'start': 2159.6, 'end': 2315.9, 'stage': 'fingerprint'}]

    def test_estimated_span_clamped_to_duration(self):
        validator = AdValidator(episode_duration=3600.0)
        ad = {'start': 3550.0, 'end': 3700.0, 'confidence': 0.9,
              'reason': 'outro pattern', 'detection_stage': 'text_pattern',
              'span_estimated': True, 'text_start': 3550.0, 'text_end': 3570.0}
        result = validator.validate([ad])
        assert result.ads[0]['end'] == 3600.0

    def test_negative_member_start_clamped_to_zero(self):
        validator = AdValidator(episode_duration=2315.9)
        ad = {'start': -2.0, 'end': 60.0, 'confidence': 0.9,
              'reason': 'preroll', 'detection_stage': 'text_pattern',
              'merged_distinct_ads': True,
              'merged_protected_start': -2.0,
              'merged_protected_end': 60.0,
              'merged_member_spans': [
                  {'start': -2.0, 'end': 40.0, 'stage': 'fingerprint'}]}
        result = validator.validate([ad])
        out = result.ads[0]
        assert out['merged_member_spans'] == [
            {'start': 0.0, 'end': 40.0, 'stage': 'fingerprint'}]
