export type AdReviewMode = 'review' | 'create' | 'span';

interface ModeCapabilities {
  ariaLabel: string;
  heading: string;
  headingOnPhone: boolean;
  // Review follows the audioMode prop; the others always play the original.
  followsAudioMode: boolean;
  fullEpisodeWindow: boolean;
  audioToggle: boolean;
  addNewButton: boolean;
  detectionMeta: boolean;
  categoryField: boolean;
  // Sponsor prompt and the sponsor/template/scope fields for a net-new ad.
  createFields: boolean;
  sponsorPrompt: boolean;
  decisionShortcuts: boolean;
  backdropCloses: boolean;
  // Snap the first play to the ad start when the playhead is far before it.
  seekIntoAdOnPlay: boolean;
  maxSelectionSeconds: number | null;
  footer: AdReviewMode;
}

const MAX_SPAN_SECONDS = 600;

export const MODE_CAPABILITIES: Record<AdReviewMode, ModeCapabilities> = {
  review: {
    ariaLabel: 'Review ad',
    heading: 'Detected ad',
    headingOnPhone: false,
    followsAudioMode: true,
    fullEpisodeWindow: false,
    audioToggle: true,
    addNewButton: true,
    detectionMeta: true,
    categoryField: true,
    createFields: false,
    sponsorPrompt: true,
    decisionShortcuts: true,
    backdropCloses: true,
    seekIntoAdOnPlay: true,
    maxSelectionSeconds: null,
    footer: 'review',
  },
  create: {
    ariaLabel: 'Add new ad',
    heading: 'Add new ad',
    headingOnPhone: false,
    followsAudioMode: false,
    fullEpisodeWindow: true,
    audioToggle: false,
    addNewButton: false,
    detectionMeta: false,
    categoryField: false,
    createFields: true,
    sponsorPrompt: false,
    decisionShortcuts: true,
    backdropCloses: false,
    seekIntoAdOnPlay: false,
    maxSelectionSeconds: null,
    footer: 'create',
  },
  span: {
    ariaLabel: 'Edit edges',
    heading: 'Edit edges',
    headingOnPhone: true,
    followsAudioMode: false,
    fullEpisodeWindow: false,
    audioToggle: false,
    addNewButton: false,
    detectionMeta: false,
    categoryField: false,
    createFields: false,
    sponsorPrompt: false,
    decisionShortcuts: false,
    backdropCloses: false,
    seekIntoAdOnPlay: true,
    maxSelectionSeconds: MAX_SPAN_SECONDS,
    footer: 'span',
  },
};
