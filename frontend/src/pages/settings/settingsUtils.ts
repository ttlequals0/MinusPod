export const BYTES_PER_MB = 1048576;

import type { ClaudeModel, UpdateSettingsPayload } from '../../api/types';
import { formatTimestamp } from '../../utils/format';

type SettingScalar = string | number | boolean;

export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatDuration(seconds?: number): string {
  if (!seconds) return '0:00';
  return formatTimestamp(seconds);
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

export function formatCost(cost: number): string {
  return `$${cost.toFixed(2)}`;
}

export function formatStorage(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

export interface StageProviderSlots {
  detectionProvider: string;
  verificationProvider: string;
  chaptersProvider: string;
  reviewProvider: string;
}

// Disabling the secondary provider must not leave a stage pointed at an
// orphaned 'secondary' slot, so any stage on it falls back to 'primary'.
// Enabling never touches these values. It only unlocks the option.
export function reconcileStageSlotsForSecondaryToggle(
  enabled: boolean,
  slots: StageProviderSlots,
): StageProviderSlots {
  if (enabled) return slots;
  const toPrimary = (slot: string) => (slot === 'secondary' ? 'primary' : slot);
  return {
    detectionProvider: toPrimary(slots.detectionProvider),
    verificationProvider: toPrimary(slots.verificationProvider),
    chaptersProvider: toPrimary(slots.chaptersProvider),
    reviewProvider: toPrimary(slots.reviewProvider),
  };
}

// Payload keys that must reach the backend in their own PUT, ahead of
// everything else in the same save. The backend applies the secondary
// provider fields after the primary provider fields within one PUT, so a
// combined save (e.g. enabling secondary, routing a stage to it, and
// changing llmProvider all at once) would have the primary provider's
// model-pruning check read pre-write secondary state.
export const SECONDARY_SETTINGS_KEYS: (keyof UpdateSettingsPayload)[] = [
  'secondaryProviderEnabled', 'secondaryProvider', 'secondaryProviderBaseUrl',
];

// Splits a changed-fields payload so the secondary provider fields can be
// sent in their own leading request, ahead of everything else, regardless
// of which other fields are also dirty in the same save.
export function splitSecondaryProviderPayload(payload: UpdateSettingsPayload): {
  secondaryPayload: UpdateSettingsPayload;
  restPayload: UpdateSettingsPayload;
} {
  const secondaryPayload: UpdateSettingsPayload = {};
  const restPayload: UpdateSettingsPayload = {};
  for (const [key, value] of Object.entries(payload)) {
    const target = SECONDARY_SETTINGS_KEYS.includes(key as keyof UpdateSettingsPayload)
      ? secondaryPayload : restPayload;
    (target as Record<string, SettingScalar>)[key] = value as SettingScalar;
  }
  return { secondaryPayload, restPayload };
}

export function formatModelLabel(model: ClaudeModel): string {
  if (model.inputCostPerMtok != null && model.outputCostPerMtok != null) {
    const fmtIn = model.inputCostPerMtok % 1 === 0
      ? model.inputCostPerMtok.toFixed(0) : model.inputCostPerMtok.toFixed(2);
    const fmtOut = model.outputCostPerMtok % 1 === 0
      ? model.outputCostPerMtok.toFixed(0) : model.outputCostPerMtok.toFixed(2);
    return `${model.name} ($${fmtIn} / $${fmtOut} per MTok)`;
  }
  return model.name;
}
