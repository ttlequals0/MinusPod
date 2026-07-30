/**
 * Shared fixtures for the stage-tunables settings tests.
 */
import { screen } from '@testing-library/react';
import type { StageTunableEntry, StageTunables } from '../../api/types';

export function entry<T>(value: T): StageTunableEntry<T> {
  return { value, isDefault: true, envOverride: null };
}

export const baseTunables: StageTunables = {
  detectionTemperature: entry(null),
  detectionMaxTokens: entry(null),
  detectionReasoningBudget: entry(null),
  detectionReasoningLevel: entry(null),
  verificationTemperature: entry(null),
  verificationMaxTokens: entry(null),
  verificationReasoningBudget: entry(null),
  verificationReasoningLevel: entry(null),
  reviewerTemperature: entry(null),
  reviewerMaxTokens: entry(null),
  reviewerReasoningBudget: entry(null),
  reviewerReasoningLevel: entry(null),
  chapterBoundaryTemperature: entry(null),
  chapterBoundaryMaxTokens: entry(null),
  chapterBoundaryReasoningBudget: entry(null),
  chapterBoundaryReasoningLevel: entry(null),
  chapterTitleTemperature: entry(null),
  chapterTitleMaxTokens: entry(null),
  chapterTitleReasoningBudget: entry(null),
  chapterTitleReasoningLevel: entry(null),
  ollamaNumCtx: entry(null),
  windowSizeSeconds: entry(null),
  windowOverlapSeconds: entry(null),
  chapterTargetSeconds: entry(null),
  chapterWindowSeconds: entry(null),
  chapterMaxBoundaries: entry(null),
  chapterMinDurationSeconds: entry(null),
};

export const baseDefaults: Record<keyof StageTunables, number | string | null> = {
  detectionTemperature: 0,
  detectionMaxTokens: 4096,
  detectionReasoningBudget: null,
  detectionReasoningLevel: null,
  verificationTemperature: 0,
  verificationMaxTokens: 4096,
  verificationReasoningBudget: null,
  verificationReasoningLevel: null,
  reviewerTemperature: 0,
  reviewerMaxTokens: 4096,
  reviewerReasoningBudget: null,
  reviewerReasoningLevel: null,
  chapterBoundaryTemperature: 0.1,
  chapterBoundaryMaxTokens: 300,
  chapterBoundaryReasoningBudget: null,
  chapterBoundaryReasoningLevel: null,
  chapterTitleTemperature: 0.1,
  chapterTitleMaxTokens: 300,
  chapterTitleReasoningBudget: null,
  chapterTitleReasoningLevel: null,
  ollamaNumCtx: null,
  windowSizeSeconds: 600,
  windowOverlapSeconds: 180,
  chapterTargetSeconds: 600,
  chapterWindowSeconds: 2700,
  chapterMaxBoundaries: 40,
  chapterMinDurationSeconds: 180,
};

// Finds the number input in the field row headed by the given label.
export function inputFor(label: string): HTMLInputElement {
  const heading = screen.getByText(label);
  const group = heading.closest('div')?.parentElement as HTMLElement;
  return group.querySelector('input[type="number"]') as HTMLInputElement;
}
