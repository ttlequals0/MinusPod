import { useState, useEffect, useRef } from 'react';

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

interface StatusData {
  currentJob: ProcessingJob | null;
  queueLength: number;
  queuedEpisodes: QueuedEpisode[];
  feedRefreshes: FeedRefresh[];
  lastUpdated: number;
}

const STAGE_LABELS: Record<string, string> = {
  downloading: 'Downloading',
  transcribing: 'Transcribing',
  detecting: 'Detecting ads',
  processing: 'Processing audio',
  complete: 'Complete',
};

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.floor(seconds)}s`;
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

function GlobalStatusBar() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);

  // Update elapsed time every second when there's a current job
  useEffect(() => {
    if (!status?.currentJob) {
      setElapsed(0);
      return;
    }

    const interval = setInterval(() => {
      if (status?.currentJob?.startedAt) {
        setElapsed(Date.now() / 1000 - status.currentJob.startedAt);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [status?.currentJob?.startedAt]);

  useEffect(() => {
    function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const eventSource = new EventSource('/api/v1/status/stream');
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        setIsConnected(true);
      };

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as StatusData;
          setStatus(data);
          if (data.currentJob) {
            setElapsed(data.currentJob.elapsed);
          }
        } catch (e) {
          console.error('Failed to parse status data:', e);
        }
      };

      eventSource.onerror = () => {
        setIsConnected(false);
        eventSource.close();

        // Reconnect after a delay
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
        }
        reconnectTimeoutRef.current = window.setTimeout(connect, 5000);
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
  }, []);

  // Don't show if no activity
  const hasActivity = status?.currentJob || (status?.queueLength ?? 0) > 0 || (status?.feedRefreshes?.length ?? 0) > 0;
  if (!hasActivity) {
    return null;
  }

  const currentJob = status?.currentJob;
  const stageLabel = currentJob ? STAGE_LABELS[currentJob.stage] || currentJob.stage : '';

  return (
    <div
      className={`fixed top-0 left-0 right-0 z-50 bg-card border-b border-border shadow-sm transition-all duration-300 ${
        hasActivity ? 'translate-y-0' : '-translate-y-full'
      }`}
    >
      {/* Collapsed View */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-2 flex items-center gap-3 hover:bg-accent/50 transition-colors"
        aria-expanded={isExpanded}
        aria-label={isExpanded ? 'Collapse status bar' : 'Expand status bar'}
      >
        {/* Connection indicator */}
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${
            isConnected ? 'bg-green-500' : 'bg-yellow-500 animate-pulse'
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
            <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden flex-shrink-0">
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${currentJob.progress}%` }}
              />
            </div>

            {/* Elapsed time */}
            <span className="text-xs text-muted-foreground flex-shrink-0 w-14 text-right">
              {formatDuration(elapsed)}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">
            Processing queue active
          </span>
        )}

        {/* Queue badge */}
        {(status?.queueLength ?? 0) > 0 && (
          <span className="px-1.5 py-0.5 text-xs font-medium bg-primary/10 text-primary rounded flex-shrink-0">
            +{status?.queueLength} queued
          </span>
        )}

        {/* Expand/collapse icon */}
        <svg
          className={`w-4 h-4 text-muted-foreground transition-transform flex-shrink-0 ${
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
        <div className="px-4 pb-3 border-t border-border/50 bg-accent/20">
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
                <div className="text-right flex-shrink-0">
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
                    <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                    <span className="truncate">{refresh.podcastName}</span>
                    {refresh.newEpisodes > 0 && (
                      <span className="text-green-500 font-medium">
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
