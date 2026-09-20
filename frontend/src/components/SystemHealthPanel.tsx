import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { PodpingCheck, SystemStatus } from '../api/types';
import { requestPodpingCheck } from '../api/settings';
import { formatDateTime } from '../utils/format';
import { focusRing } from './fieldStyles';
import ChevronCaret from './ChevronCaret';
import { badgeBase, tint } from './badgeStyles';
import { btnSecondary, touchTarget } from './buttonStyles';

const NODE_STALE_MS = 10 * 60 * 1000;

type Health = 'healthy' | 'warning' | 'critical';

const PILL: Record<Health, string> = {
  healthy: tint.success,
  warning: tint.warning,
  critical: tint.destructive,
};
const DOT: Record<Health | 'neutral', string> = {
  healthy: 'bg-success',
  warning: 'bg-warning',
  critical: 'bg-destructive',
  neutral: 'bg-muted-foreground/40',
};
const LABEL: Record<Health, string> = {
  healthy: 'Healthy',
  warning: 'Warning',
  critical: 'Critical',
};

function checking(check?: PodpingCheck): boolean {
  return check?.status === 'pending' || check?.status === 'running';
}

function nodeFresh(node: NonNullable<SystemStatus['podping']>['nodes'][number], now: number): boolean {
  if (node.consecutiveFailures > 0 || !node.lastSuccessAt) return false;
  const lastSuccess = Date.parse(node.lastSuccessAt);
  return Number.isFinite(lastSuccess) && now - lastSuccess <= NODE_STALE_MS;
}

function podpingHealth(p: SystemStatus['podping'], now = Date.now()): Health | 'neutral' {
  if (!p) return 'neutral';
  if (!p.listenerEnabled) return p.check?.status === 'error' ? 'warning' : 'neutral';
  if (p.allNodesDown) return 'critical';
  if (p.check?.status === 'error') return 'warning';
  const active = p.nodes.find((node) => node.active);
  if (!active) {
    const observed = p.nodes.some((node) =>
      node.lastSuccessAt || node.consecutiveFailures > 0 || node.outcome,
    );
    return observed ? 'warning' : 'neutral';
  }
  if (!nodeFresh(active, now)) return 'warning';
  return p.nodes.some((node) => node !== active && nodeFresh(node, now)) ? 'healthy' : 'warning';
}

// Single rollup across the health signals: transcriber down or every Podping
// node down is critical; a degraded refresh, a partial Podping outage, or a
// failed last transcription is a warning; otherwise healthy.
export function rollupHealth(status: SystemStatus): Health {
  const t = status.transcriber;
  const p = status.podping;
  const f = status.feedRefresh;
  const podping = podpingHealth(p);
  if (t?.available === false || podping === 'critical') return 'critical';
  if (f?.outageDegraded || podping === 'warning' || t?.lastOutcome?.status === 'failed') {
    return 'warning';
  }
  return 'healthy';
}

function Row({ tone, label, detail }: { tone: Health | 'neutral'; label: string; detail?: string }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${DOT[tone]}`} aria-hidden="true" />
      <div className="min-w-0">
        <span className="text-foreground">{label}</span>
        {detail && <span className="text-muted-foreground"> {detail}</span>}
      </div>
    </div>
  );
}

function TranscriberRow({ t }: { t: NonNullable<SystemStatus['transcriber']> }) {
  const device = t.device ?? 'unknown';
  const state = t.available ? 'available' : 'unavailable';
  let detail = `${t.backend} (${device}), ${state}`;
  if (t.lastOutcome?.status) {
    const at = t.lastOutcome.observedAt ? ` at ${formatDateTime(t.lastOutcome.observedAt)}` : '';
    detail += `; last ${t.lastOutcome.status}${at}`;
  }
  // A failed last transcription is what the rollup escalates on, so the row
  // has to carry the same warning.
  const tone: Health = !t.available
    ? 'critical'
    : t.lastOutcome?.status === 'failed' ? 'warning' : 'healthy';
  return <Row tone={tone} label="Transcriber" detail={detail} />;
}

function PodpingRow({ p }: { p: NonNullable<SystemStatus['podping']> }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [submitting, setSubmitting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  const check = p.check;
  const isChecking = submitting || checking(check);
  const tone = podpingHealth(p, now);
  const nodeState = (node: NonNullable<SystemStatus['podping']>['nodes'][number]) => {
    if (node.consecutiveFailures > 0) {
      if (node.outcome === 'invalid_response') {
        return {
          label: node.httpStatus ? `Invalid response (HTTP ${node.httpStatus})` : 'Invalid response',
          tone: 'warning' as const,
        };
      }
      return {
        label: node.httpStatus ? `HTTP ${node.httpStatus}` : 'Unreachable',
        tone: 'warning' as const,
      };
    }
    if (!node.lastSuccessAt) return { label: 'Not checked', tone: 'neutral' as const };
    const label = node.httpStatus ? `HTTP ${node.httpStatus}` : 'Healthy';
    if (!nodeFresh(node, now)) {
      return { label, tone: 'neutral' as const };
    }
    return { label, tone: 'healthy' as const };
  };
  const healthy = p.nodes.filter((node) => nodeState(node).tone === 'healthy').length;
  let detail: string;
  if (isChecking) {
    detail = 'checking nodes';
  } else if (!p.listenerEnabled) {
    detail = check?.status === 'completed'
      && typeof check.healthyNodes === 'number' && typeof check.totalNodes === 'number'
      ? `${check.healthyNodes}/${check.totalNodes} nodes healthy`
      : 'listener disabled';
  } else {
    detail = p.allNodesDown
      ? `all ${p.nodes.length} nodes down`
      : `${healthy}/${p.nodes.length} nodes healthy`;
  }
  if (p.listenerEnabled && p.degradedSince) {
    detail += `; degraded since ${formatDateTime(p.degradedSince)}`;
  }
  if (p.listenerEnabled && p.allNodesDown) detail += '; RSS polling remains independent';
  return (
    <div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls="podping-node-details"
          aria-label="Podping details"
          className={`min-w-0 flex-1 min-h-[44px] flex items-center gap-2 text-sm text-left ${focusRing}`}
        >
          <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${DOT[tone]}`} aria-hidden="true" />
          <span className="min-w-0 flex-1">
            <span className="text-foreground">Podping</span>
            <span className="text-muted-foreground"> {detail}</span>
          </span>
          <ChevronCaret expanded={open} className="w-4 h-4 shrink-0" />
        </button>
        <button
          type="button"
          disabled={isChecking}
          onClick={async () => {
            setRequestError(null);
            setSubmitting(true);
            try {
              const result = await requestPodpingCheck();
              queryClient.setQueryData<SystemStatus>(['status'], (current) => current?.podping
                ? { ...current, podping: { ...current.podping, check: result } }
                : current);
              void queryClient.invalidateQueries({ queryKey: ['status'] });
            } catch (error) {
              setRequestError(error instanceof Error ? error.message : 'Check could not start.');
            } finally {
              setSubmitting(false);
            }
          }}
          className={`${touchTarget} shrink-0 p-0 ${focusRing} disabled:opacity-50`}
        >
          <span className={`px-2 py-1 rounded text-xs ${btnSecondary} transition-colors`}>
            {isChecking ? 'Checking...' : 'Check now'}
          </span>
        </button>
      </div>
      {open && (
        <div id="podping-node-details" className="min-w-0 ml-4 mt-2 space-y-2 border-l border-border pl-3">
          {p.nodes.map((node) => {
            const state = nodeState(node);
            return (
              <div key={node.node} className="min-w-0 max-w-full flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT[state.tone]}`} aria-hidden="true" />
                <span className="font-medium text-foreground break-all">{node.node}</span>
                {node.active && <span className={`${badgeBase} ${tint.primary}`}>Active</span>}
                <span className={`${badgeBase} ${state.tone === 'healthy' ? tint.success : state.tone === 'warning' ? tint.warning : 'bg-muted text-muted-foreground'}`}>
                  {state.label}
                </span>
                {node.lastSuccessAt && (
                  <span className="text-muted-foreground">Last seen {formatDateTime(node.lastSuccessAt)}</span>
                )}
              </div>
            );
          })}
          {(requestError || check?.status === 'error') && (
            <div role="alert" className="text-xs text-destructive">
              {requestError || check?.message || 'Check failed.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FeedRefreshRow({ f }: { f: NonNullable<SystemStatus['feedRefresh']> }) {
  if (f.outageDegraded) {
    const retry = f.nextRetryAt ? `; retry ${formatDateTime(f.nextRetryAt)}` : '';
    return (
      <Row tone="warning" label="Feed refresh"
        detail={`degraded, ${f.outageAffectedCount} feed(s) affected${retry}`} />
    );
  }
  const last = f.lastSuccessfulRefreshAt ? formatDateTime(f.lastSuccessfulRefreshAt) : 'never';
  return <Row tone="healthy" label="Feed refresh" detail={`healthy; last success ${last}`} />;
}

function SystemHealthPanel({ status }: { status: SystemStatus }) {
  const [open, setOpen] = useState(false);
  if (!status.transcriber && !status.podping && !status.feedRefresh) return null;
  const overall = rollupHealth(status);
  return (
    <div className="mt-4 rounded-lg border border-border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center justify-between gap-2 min-h-[44px] px-3 py-2 ${focusRing}`}
      >
        <span className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground">System health</span>
          <span className={`${badgeBase} ${PILL[overall]}`}>
            {LABEL[overall]}
          </span>
        </span>
        <ChevronCaret expanded={open} />
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2 border-t border-border">
          {status.transcriber && <TranscriberRow t={status.transcriber} />}
          {status.podping && <PodpingRow p={status.podping} />}
          {status.feedRefresh && <FeedRefreshRow f={status.feedRefresh} />}
        </div>
      )}
    </div>
  );
}

export default SystemHealthPanel;
