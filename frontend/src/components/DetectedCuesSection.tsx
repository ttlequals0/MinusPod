import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from './CollapsibleSection';
import LoadingSpinner from './LoadingSpinner';
import CueMarkModal from './CueMarkModal';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import {
  getDetectedCues,
  cueTypeLabel,
  type DetectedCue,
  type CueTemplateType,
} from '../api/cueTemplates';
import { getSettings } from '../api/settings';

interface DetectedCuesSectionProps {
  slug: string;
  episodeId: string;
  episodeTitle: string;
  episodeDuration: number;
  hasOriginalAudio: boolean;
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const PAGE_SIZE = 10;

const SOURCE_META: Record<DetectedCue['source'], { label: string; className: string }> = {
  template: { label: 'Template match', className: 'bg-violet-500/20 text-violet-600 dark:text-violet-400' },
  spectral: { label: 'Spectral cue', className: 'bg-blue-500/20 text-blue-600 dark:text-blue-400' },
  loud_spot: { label: 'Loud spot', className: 'bg-muted text-muted-foreground' },
};

function cueKey(c: DetectedCue): string {
  return `${c.source}:${c.start}`;
}

function DetectedCuesSection({
  slug, episodeId, episodeTitle, episodeDuration, hasOriginalAudio,
}: DetectedCuesSectionProps) {
  const queryClient = useQueryClient();
  // Shares the CollapsibleSection's persisted open state (same storage key) so a
  // section restored open on mount still enables the lazy query.
  const [expanded, setExpanded] = useLocalStorageState<boolean>('episode-detected-cues', false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [seed, setSeed] = useState<{ start: number; end: number } | null>(null);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 4;

  // Lazy: the loud-spot pass decodes the original audio, so only run it once the
  // user opens the section.
  const cuesQuery = useQuery({
    queryKey: ['detected-cues', slug, episodeId],
    queryFn: () => getDetectedCues(slug, episodeId),
    enabled: expanded,
  });

  const cues = (cuesQuery.data?.detectedCues ?? []).filter((c) => !dismissed.has(cueKey(c)));
  const visible = cues.slice(0, visibleCount);

  return (
    <CollapsibleSection
      title="Detected Cues"
      subtitle="Turn a detected sound into a template"
      defaultOpen={false}
      storageKey="episode-detected-cues"
      onToggle={setExpanded}
    >
      <p className="text-sm text-muted-foreground mb-4">
        Candidate ad-break sounds in this episode. Make a template from one to match it on future episodes of this feed.
      </p>

      {cuesQuery.isLoading && <LoadingSpinner size="sm" className="my-2" />}
      {cuesQuery.error && (
        <p className="text-sm text-destructive">Could not load detected cues.</p>
      )}
      {cuesQuery.data && cues.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No candidate cues found in this episode.
        </p>
      )}

      <div className="space-y-2">
        {visible.map((c) => {
          const src = SOURCE_META[c.source];
          return (
            <div key={cueKey(c)} className="p-3 bg-secondary/40 rounded-lg border border-border">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-foreground">
                    {formatTime(c.start)} - {formatTime(c.end)}
                  </span>
                  <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${src.className}`}>
                    {src.label}
                  </span>
                  {c.label && c.cueType && (
                    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground">
                      {cueTypeLabel(c.cueType as CueTemplateType)}
                    </span>
                  )}
                  {c.score != null && (
                    <span className="text-xs text-muted-foreground">Match {Math.round(c.score * 100)}%</span>
                  )}
                  {c.score == null && c.prominenceDb != null && (
                    <span className="text-xs text-muted-foreground">{c.prominenceDb.toFixed(1)} dB</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => setSeed({ start: c.start, end: Math.min(c.end, c.start + captureMaxSeconds) })}
                    disabled={!hasOriginalAudio}
                    title={hasOriginalAudio ? 'Open the capture tool to make a template'
                      : 'Original audio not retained for this episode'}
                    className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                  >
                    Make template
                  </button>
                  <button
                    onClick={() => setDismissed((prev) => new Set(prev).add(cueKey(c)))}
                    className="px-3 py-2 sm:py-1 text-sm sm:text-xs rounded border border-border text-muted-foreground hover:bg-secondary transition-colors touch-manipulation min-h-[40px] sm:min-h-0"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {cues.length > visible.length && (
        <button
          onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
          className="mt-3 w-full sm:w-auto px-3 py-2 text-sm rounded border border-border text-muted-foreground hover:bg-secondary transition-colors touch-manipulation"
        >
          Load more ({cues.length - visible.length} remaining)
        </button>
      )}

      {seed && (
        <CueMarkModal
          podcastSlug={slug}
          episodeId={episodeId}
          episodeTitle={episodeTitle}
          episodeDuration={episodeDuration}
          initialStart={seed.start}
          initialEnd={seed.end}
          captureMinSeconds={captureMinSeconds}
          captureMaxSeconds={captureMaxSeconds}
          onClose={() => setSeed(null)}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ['cue-templates', slug] })}
          onFinalSave={() => { setSeed(null); cuesQuery.refetch(); }}
        />
      )}
    </CollapsibleSection>
  );
}

export default DetectedCuesSection;
