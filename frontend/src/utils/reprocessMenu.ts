interface MenuCopy { subtitle: string; tooltip: string }

export interface ReprocessMenuCopy {
  reprocess: MenuCopy;
  full: MenuCopy;
  redetect: MenuCopy;
}

const DEFAULT_COPY: ReprocessMenuCopy = {
  reprocess: {
    subtitle: 'Use patterns + AI',
    tooltip: 'Use learned patterns + AI analysis',
  },
  full: {
    subtitle: 'Skip patterns, AI only',
    tooltip: 'Skip pattern DB, AI analyzes everything fresh',
  },
  redetect: {
    subtitle: 'Keep transcript, re-cut',
    tooltip: 'Re-run ad detection and re-cut using the existing transcript (skips re-transcription)',
  },
};

const YOUTUBE_COPY: ReprocessMenuCopy = {
  reprocess: {
    subtitle: 'SponsorBlock marks + patterns',
    tooltip: 'Refresh SponsorBlock marks and captions; learned patterns run, the LLM only when there are no marks',
  },
  full: {
    subtitle: 'Marks, patterns + pass 1 LLM',
    tooltip: 'Also run pass 1 LLM detection even though SponsorBlock has marks',
  },
  redetect: {
    subtitle: 'Keep transcript, add pass 1 LLM',
    tooltip: 'Keep the transcript and run pass 1 LLM detection on it',
  },
};

export function reprocessModeKey(mode: string): keyof ReprocessMenuCopy {
  return mode === 'llm' ? 'redetect' : mode === 'full' ? 'full' : 'reprocess';
}

export function reprocessMenuCopy(feedType?: string): ReprocessMenuCopy {
  return feedType === 'youtube' ? YOUTUBE_COPY : DEFAULT_COPY;
}
