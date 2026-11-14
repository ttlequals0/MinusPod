import type { ComponentProps } from 'react';
import type { ReviewDetection } from '../../api/detections';
import type AdReviewModal from '../../components/AdReviewModal';
import type { AdReviewItem } from '../../components/AdReviewModal';

type DetectionReviewProps = Pick<
  ComponentProps<typeof AdReviewModal>,
  'item' | 'episodeDuration' | 'hasOriginal' | 'audioMode' | 'processedAudioUrl'
>;

export function adReviewProps(d: ReviewDetection): DetectionReviewProps {
  return {
    item: {
      podcastSlug: d.feedSlug,
      episodeId: d.episodeId,
      start: d.start,
      end: d.end,
      sponsor: d.sponsor,
      reason: d.reason,
      confidence: d.confidence,
      detectionStage: d.detectionStage,
      patternId: d.patternId,
      correctedBounds: null,
      category: d.category as AdReviewItem['category'],
      actionApplied: d.actionApplied,
    } satisfies AdReviewItem,
    episodeDuration: d.episodeDuration ?? 0,
    hasOriginal: d.hasOriginalAudio,
    audioMode: d.hasOriginalAudio ? 'original' : 'processed',
    processedAudioUrl: d.processedUrl,
  };
}

export function splitTarget(d: ReviewDetection) {
  return {
    podcastSlug: d.feedSlug,
    episodeId: d.episodeId,
    start: d.start,
    end: d.end,
  };
}
