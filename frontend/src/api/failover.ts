import { apiRequest } from './client';
import { SLOT_LABELS } from './types';

export type FailoverTargetName = 'llm-a' | 'llm-b' | 'transcriber';
export type FailoverProbeName =
  | 'llm-a' | 'llm-b' | 'llm-failover' | 'transcriber' | 'transcriber-failover';

export interface FailoverTargetState {
  active: boolean;
  source: 'auto' | 'manual' | 'probe' | null;
  since: string | null;
  reason: string | null;
  configured: boolean;
  // False for Provider B while it is switched off; always true for llm-a and transcriber.
  enabled: boolean;
}

export interface FailoverProbe {
  reachable: boolean | null;
  status: number | null;
  detail: string;
  checkedAt: string | null;
  healthyStreak: number;
  failedStreak: number;
}

export interface FailoverEvent {
  id: number;
  target: string;
  action: 'trigger' | 'cancel';
  source: string;
  reason: string | null;
  createdAt: string;
}

export interface FailoverOverview {
  targets: Record<FailoverTargetName, FailoverTargetState>;
  probes: Record<FailoverProbeName, FailoverProbe>;
  policy: { probeIntervalMinutes: number; recoveryProbes: number };
  events: FailoverEvent[];
}

export function getFailover(): Promise<FailoverOverview> {
  return apiRequest<FailoverOverview>('/failover');
}

export function triggerFailover(
  target: FailoverTargetName,
  reason?: string,
): Promise<{ target: string; state: FailoverTargetState }> {
  return apiRequest(`/failover/${target}/trigger`, {
    method: 'POST',
    body: reason === undefined ? {} : { reason },
  });
}

export function cancelFailover(
  target: FailoverTargetName,
): Promise<{ target: string; state: FailoverTargetState }> {
  return apiRequest(`/failover/${target}/cancel`, { method: 'POST' });
}

export function probeFailover(): Promise<{ probes: FailoverOverview['probes'] }> {
  return apiRequest('/failover/probe', { method: 'POST' });
}

export const failoverQueryKey = ['failover'] as const;

export const FAILOVER_TARGETS: FailoverTargetName[] = ['llm-a', 'llm-b', 'transcriber'];

export const FAILOVER_TARGET_LABELS: Record<FailoverTargetName, string> = {
  'llm-a': SLOT_LABELS.primary,
  'llm-b': SLOT_LABELS.secondary,
  transcriber: 'Transcriber',
};

// Event rows and status frames carry the name as a plain string.
export function failoverTargetLabel(name: string): string {
  return FAILOVER_TARGET_LABELS[name as FailoverTargetName] ?? name;
}

// How a target entered failover, phrased to follow "via".
export const FAILOVER_SOURCE_LABELS: Record<string, string> = {
  auto: 'a failed request',
  probe: 'a failed probe',
  manual: 'manual trigger',
};

export function failoverSourceLabel(source: string): string {
  return FAILOVER_SOURCE_LABELS[source] ?? source;
}
