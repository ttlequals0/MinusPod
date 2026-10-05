import type { FailoverProbe, FailoverTargetState } from '../api/failover';

export function makeFailoverTarget(overrides: Partial<FailoverTargetState> = {}): FailoverTargetState {
  return { active: false, source: null, since: null, reason: null, configured: true, ...overrides };
}

export function makeFailoverProbe(overrides: Partial<FailoverProbe> = {}): FailoverProbe {
  return {
    reachable: null, status: null, detail: '', checkedAt: null, healthyStreak: 0, failedStreak: 0, ...overrides,
  };
}
