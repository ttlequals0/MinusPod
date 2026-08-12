// Sources set by AdValidator._audio_corroboration_source when audio evidence
// backs a marker (key 'corroborated_by' on the ad dict).
export type CorroborationSource =
  | 'transition_pair'
  | 'template_cue'
  | 'volume_anomaly'
  | 'splice_evidence'
  | 'dai_differential';

// Corroboration is an agreement signal, so every source shares the success tint.
export const CORROBORATION_CLASS = 'bg-success/20 text-success';

export const CORROBORATION_META: Record<CorroborationSource, { label: string; title: string }> = {
  transition_pair: {
    label: 'Corroborated: transition',
    title: 'A dynamic-ad transition pair sits within 5s of this ad boundary.',
  },
  template_cue: {
    label: 'Corroborated: cue',
    title: 'A learned ad-break cue template fires within 5s of this ad boundary.',
  },
  volume_anomaly: {
    label: 'Corroborated: volume',
    title: 'A large volume step sits within 5s of this ad boundary.',
  },
  splice_evidence: {
    label: 'Corroborated: splice',
    title: 'A splice artifact (encoded silence or an abrupt level or spectral step) sits within 3s of this ad boundary.',
  },
  dai_differential: {
    label: 'Corroborated: cross-fetch',
    title: 'A second fetch of this episode had different audio overlapping this span, so it was dynamically inserted.',
  },
};
