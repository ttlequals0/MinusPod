import { useState, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { storeLoginRedirect } from '../utils/loginRedirect';
import { getStageLabel } from '../utils/processingStage';
import { focusRing } from './fieldStyles';

interface ProcessingJob {
  slug: string;
  episodeId: string;
  title: string;
  podcastName: string;
  stage: string;
  progress: number;
  startedAt: number;
  elapsed: number;
}

interface QueuedEpisode {
  slug: string;
  episodeId: string;
  title: string;
  podcastName: string;
  queuedAt: number;
}

interface FeedRefresh {
  slug: string;
  podcastName: string;
  newEpisodes: number;
  startedAt: number;
}

interface OfflineService {
  service: string;
  held: number;
  // null until the maintenance tick has probed once.
  reachable: boolean | null;
  checkedAt: string | null;
}

interface QueueHold {
  queuePaused: boolean;
  holdUntil: string | null;
  rateLimitHeld: number;
  offlineHeld: number;
  offlineServices: OfflineService[];
}

interface StatusData {
  currentJob: ProcessingJob | null;
  queueLength: number;
  queuedEpisodes: QueuedEpisode[];
  feedRefreshes: FeedRefresh[];
  hold?: QueueHold;
  lastUpdated: number;
}

const SERVICE_LABELS: Record<string, string> = {
  llm: 'LLM provider',
  whisper: 'Whisper endpoint',
};

function serviceLabel(service: string): string {
  return SERVICE_LABELS[service] ?? service;
}

function countLabel(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

/** Clock time without seconds: the precision is noise on a wait of minutes. */
function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/** "in 12m" / "in 2h 5m", or null once the time has passed. */
function formatRelative(iso: string): string | null {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return 'in under a minute';
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `in ${mins}m`;
  return `in ${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/** When the queue resumes, as one sentence. */
function resumesText(iso: string | null): string {
  if (!iso) return 'Resumes once the provider window resets.';
  const relative = formatRelative(iso);
  return `Resumes ${formatClock(iso)}${relative ? ` (${relative})` : ''}.`;
}

/** Short summary for the collapsed bar, or null when nothing is held. */
function holdSummary(hold: QueueHold | undefined): string | null {
  if (!hold) return null;
  if (hold.queuePaused) return 'Queue paused';
  if (hold.offlineHeld > 0) {
    const down = hold.offlineServices.filter((s) => s.reachable === false);
    return down.length === 1
      ? `${serviceLabel(down[0].service)} unreachable`
      : 'Waiting on a service';
  }
  if (hold.rateLimitHeld > 0) return 'Episodes held';
  return null;
}


function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.floor(seconds)}s`;
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

// SSE reconnection constants
const SSE_INITIAL_DELAY = 1000;  // Start with 1 second
const SSE_MAX_DELAY = 30000;     // Max 30 seconds
const SSE_BACKOFF_MULTIPLIER = 2;

function GlobalStatusBar() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [, setReconnectAttempt] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const prevStatusRef = useRef<StatusData | null>(null);
  const queryClient = useQueryClient();

  // Reset the elapsed counter when the current job changes (during render).
  const currentJobStarted = status?.currentJob?.startedAt;
  const [lastJobStarted, setLastJobStarted] = useState(currentJobStarted);
  if (currentJobStarted !== lastJobStarted) {
    setLastJobStarted(currentJobStarted);
    setElapsed(0);
  }

  // Tick the elapsed counter every second while a job is running.
  useEffect(() => {
    if (!currentJobStarted) return;
    const interval = setInterval(() => {
      setElapsed(Date.now() / 1000 - currentJobStarted);
    }, 1000);
    return () => clearInterval(interval);
  }, [currentJobStarted]);

  useEffect(() => {
    function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const eventSource = new EventSource('/api/v1/status/stream');
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        setIsConnected(true);
        setReconnectAttempt(0); // Reset backoff on successful connection
      };

      // EventSource cannot see HTTP 401; the backend emits an
      // application-level auth-failed event when the session has lapsed,
      // so we listen for it and redirect to /login. Without this the
      // bar would silently reconnect-loop against a route that now
      // requires auth.
      eventSource.addEventListener('auth-failed', () => {
        eventSource.close();
        if (!window.location.pathname.includes('/login')) {
          storeLoginRedirect(window.location.pathname, window.location.search);
          window.location.href = '/ui/login';
        }
      });

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as StatusData;
          setStatus(data);
          if (data.currentJob) {
            setElapsed(data.currentJob.elapsed);
          }

          // Invalidate React Query caches on status transitions so
          // pages (FeedDetail, EpisodeDetail, Dashboard) pick up
          // changes without manual refresh.
          const prev = prevStatusRef.current;
          if (prev?.currentJob && !data.currentJob) {
            // Job just completed
            queryClient.invalidateQueries({ queryKey: ['episode'] });
            queryClient.invalidateQueries({ queryKey: ['episodes'] });
            queryClient.invalidateQueries({ queryKey: ['feed'] });
            queryClient.invalidateQueries({ queryKey: ['feeds'] });
          }
          if (prev?.feedRefreshes?.length &&
              data.feedRefreshes.length < prev.feedRefreshes.length) {
            // Feed refresh completed
            queryClient.invalidateQueries({ queryKey: ['feeds'] });
            queryClient.invalidateQueries({ queryKey: ['episodes'] });
          }
          prevStatusRef.current = data;
        } catch (e) {
          console.error('Failed to parse status data:', e);
        }
      };

      eventSource.onerror = () => {
        setIsConnected(false);
        eventSource.close();

        // Calculate exponential backoff delay
        setReconnectAttempt((prev) => {
          const attempt = prev + 1;
          const delay = Math.min(
            SSE_INITIAL_DELAY * Math.pow(SSE_BACKOFF_MULTIPLIER, attempt - 1),
            SSE_MAX_DELAY
          );

          console.log(`SSE reconnecting in ${delay}ms (attempt ${attempt})`);

          // Reconnect after exponential delay
          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current);
          }
          reconnectTimeoutRef.current = window.setTimeout(connect, delay);

          return attempt;
        });
      };
    }

    connect();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
    // queryClient is stable across renders (react-query); SSE connect is
    // intentionally one-shot on mount, not re-keyed off the client identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A hold counts as activity: an idle queue that is paused or waiting on a
  // service looks identical to an empty one, which is the case worth surfacing.
  const hold = status?.hold;
  const summary = holdSummary(hold);
  const hasActivity = status?.currentJob || (status?.queueLength ?? 0) > 0
    || (status?.feedRefreshes?.length ?? 0) > 0 || summary !== null;
  if (!hasActivity) {
    return null;
  }

  const currentJob = status?.currentJob;
  const stageLabel = currentJob ? getStageLabel(currentJob.stage) : '';

  return (
    <div
      className={`fixed top-0 left-0 right-0 z-50 bg-card border-b border-border shadow-xs transition-all duration-300 ${
        hasActivity ? 'translate-y-0' : '-translate-y-full'
      }`}
    >
      {/* Collapsed View */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className={`w-full px-4 py-2 flex items-center gap-3 hover:bg-accent/50 transition-colors ${focusRing}`}
        aria-expanded={isExpanded}
        aria-label={isExpanded ? 'Collapse status bar' : 'Expand status bar'}
      >
        {/* Connection indicator */}
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${
            isConnected ? 'bg-success' : 'bg-warning animate-pulse'
          }`}
          aria-label={isConnected ? 'Connected' : 'Reconnecting'}
        />

        {/* Current job info */}
        {currentJob ? (
          <>
            <div className="flex-1 min-w-0 flex items-center gap-2">
              <span className="text-xs font-medium text-primary truncate">
                {stageLabel}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {currentJob.title}
              </span>
            </div>

            {/* Progress bar */}
            <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden shrink-0">
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${currentJob.progress}%` }}
              />
            </div>

            {/* Elapsed time */}
            <span className="text-xs text-muted-foreground shrink-0 w-14 text-right">
              {formatDuration(elapsed)}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">
            {summary ?? 'Processing queue active'}
          </span>
        )}

        {/* Hold badge: amber so a stalled queue reads differently from a busy one */}
        {summary && currentJob && (
          <span className="px-1.5 py-0.5 text-xs font-medium bg-warning/10 text-warning rounded shrink-0">
            {summary}
          </span>
        )}

        {/* Queue badge */}
        {(status?.queueLength ?? 0) > 0 && (
          <span className="px-1.5 py-0.5 text-xs font-medium bg-primary/10 text-primary rounded shrink-0">
            +{status?.queueLength} queued
          </span>
        )}

        {/* Expand/collapse icon */}
        <svg
          className={`w-4 h-4 text-muted-foreground transition-transform shrink-0 ${
            isExpanded ? 'rotate-180' : ''
          }`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {/* Expanded View */}
      {isExpanded && (
        <div className="px-4 pb-3 border-t border-border/50 bg-accent/20 max-h-48 overflow-y-auto">
          {/* Current job details */}
          {currentJob && (
            <div className="py-2 border-b border-border/30">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {currentJob.title}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">
                    {currentJob.podcastName}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-sm font-medium text-primary">{stageLabel}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDuration(elapsed)}
                  </p>
                </div>
              </div>
              <div className="mt-2 h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all duration-300"
                  style={{ width: `${currentJob.progress}%` }}
                />
              </div>
            </div>
          )}

          {/* Queue holds: why work is not moving, and when it resumes */}
          {summary && hold && (
            <div className="py-2 border-b border-border/30">
              <p className="text-xs font-medium text-warning mb-1">{summary}</p>
              <ul className="space-y-1">
                {hold.queuePaused && (
                  <li className="text-xs text-foreground">
                    {`Provider rate limit. ${resumesText(hold.holdUntil)}`}
                    {hold.rateLimitHeld > 0
                      && ` ${countLabel(hold.rateLimitHeld, 'episode')} waiting.`}
                  </li>
                )}
                {hold.offlineServices.map((svc) => (
                  <li key={svc.service} className="text-xs text-foreground">
                    {serviceLabel(svc.service)}{' '}
                    {svc.reachable === false ? 'unreachable' : 'not checked yet'}.{' '}
                    {countLabel(svc.held, 'episode')} waiting
                    {svc.checkedAt
                      ? `, last checked ${formatClock(svc.checkedAt)}`
                      : ''}
                    . Others keep processing.
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Queued episodes */}
          {(status?.queuedEpisodes?.length ?? 0) > 0 && (
            <div className="py-2">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Queued ({status?.queuedEpisodes.length})
              </p>
              <ul className="space-y-1">
                {status?.queuedEpisodes.slice(0, 3).map((ep) => (
                  <li
                    key={`${ep.slug}-${ep.episodeId}`}
                    className="text-xs text-foreground truncate"
                  >
                    <span className="text-muted-foreground">{ep.podcastName}:</span>{' '}
                    {ep.title}
                  </li>
                ))}
                {(status?.queuedEpisodes.length ?? 0) > 3 && (
                  <li className="text-xs text-muted-foreground">
                    +{(status?.queuedEpisodes.length ?? 0) - 3} more
                  </li>
                )}
              </ul>
            </div>
          )}

          {/* Feed refreshes */}
          {(status?.feedRefreshes?.length ?? 0) > 0 && (
            <div className="py-2 border-t border-border/30">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Feed Refreshes
              </p>
              <ul className="space-y-1">
                {status?.feedRefreshes.map((refresh) => (
                  <li
                    key={refresh.slug}
                    className="text-xs text-foreground flex items-center gap-1"
                  >
                    <span className="w-2 h-2 rounded-full bg-c-blue animate-pulse" />
                    <span className="truncate">{refresh.podcastName}</span>
                    {refresh.newEpisodes > 0 && (
                      <span className="text-success font-medium">
                        +{refresh.newEpisodes} new
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default GlobalStatusBar;
