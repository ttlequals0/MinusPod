import { useEffect, useState } from 'react';
import type { SystemStatus } from '../api/types';
import { formatDateTime } from '../utils/format';
import { focusRing } from './fieldStyles';
import ChevronCaret from './ChevronCaret';
import { badgeBase, tint } from './badgeStyles';

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

// A live listener with some, but not all, nodes failing. The rollup pill and
// the Podping row both read degradation from this, so they cannot disagree.
function podpingDegraded(p: SystemStatus['podping']): boolean {
  return !!p?.listenerEnabled && !p.allNodesDown
    && p.nodes.some((n) => n.consecutiveFailures > 0);
}

// Single rollup across the health signals: transcriber down or every Podping
// node down is critical; a degraded refresh, a partial Podping outage, or a
// failed last transcription is a warning; otherwise healthy.
export function rollupHealth(status: SystemStatus): Health {
  const t = status.transcriber;
  const p = status.podping;
  const f = status.feedRefresh;
  if (t?.available === false || (p?.listenerEnabled && p.allNodesDown)) return 'critical';
  if (f?.outageDegraded || podpingDegraded(p) || t?.lastOutcome?.status === 'failed') {
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
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  if (!p.listenerEnabled) {
    return <Row tone="neutral" label="Podping" detail="listener disabled" />;
  }
  const tone: Health = p.allNodesDown ? 'critical' : podpingDegraded(p) ? 'warning' : 'healthy';
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
    const lastSuccess = Date.parse(node.lastSuccessAt);
    const label = node.httpStatus ? `HTTP ${node.httpStatus}` : 'Healthy';
    if (!Number.isFinite(lastSuccess) || now - lastSuccess > NODE_STALE_MS) {
      return { label, tone: 'neutral' as const };
    }
    return { label, tone: 'healthy' as const };
  };
  const healthy = p.nodes.filter((node) => nodeState(node).tone === 'healthy').length;
  // The all-nodes-down flag is authoritative for the summary text; per-node
  // counters only describe a partial outage.
  let detail = p.allNodesDown
    ? `all ${p.nodes.length} nodes down`
    : `${healthy}/${p.nodes.length} nodes healthy`;
  if (p.degradedSince) detail += `; degraded since ${formatDateTime(p.degradedSince)}`;
  if (p.listenerEnabled && p.allNodesDown) detail += '; RSS polling remains independent';
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="podping-node-details"
        aria-label="Podping details"
        className={`w-full min-h-[44px] flex items-center gap-2 text-sm text-left ${focusRing}`}
      >
        <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${DOT[tone]}`} aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className="text-foreground">Podping</span>
          <span className="text-muted-foreground"> {detail}</span>
        </span>
        <ChevronCaret expanded={open} className="w-4 h-4 shrink-0" />
      </button>
      {open && (
        <div id="podping-node-details" className="min-w-0 ml-4 mt-2 space-y-2 border-l border-border pl-3">
          {p.nodes.map((node) => {
            const state = nodeState(node);
            return (
              <div key={node.node} className="min-w-0 max-w-full flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT[state.tone]}`} aria-hidden="true" />
                <span className="font-medium text-foreground break-all">{node.node}</span>
                <span className={`${badgeBase} ${state.tone === 'healthy' ? tint.success : state.tone === 'warning' ? tint.warning : 'bg-muted text-muted-foreground'}`}>
                  {state.label}
                </span>
                {node.lastSuccessAt && (
                  <span className="text-muted-foreground">Last seen {formatDateTime(node.lastSuccessAt)}</span>
                )}
              </div>
            );
          })}
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
