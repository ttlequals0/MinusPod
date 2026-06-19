import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from './CollapsibleSection';
import LoadingSpinner from './LoadingSpinner';
import CueMarkModal from './CueMarkModal';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import {
  getDetectedCues,
  getCueCandidates,
  cueTypeLabel,
  type DetectedCue,
  type CueCandidate,
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

const makeBtn =
  'px-3 py-2 sm:py-1 text-sm sm:text-xs rounded font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors touch-manipulation min-h-[40px] sm:min-h-0';

function CueRow({
  start, end, badge, badgeClass, meta, onMake, canMake,
}: {
  start: number; end: number; badge: string; badgeClass: string;
  meta?: string; onMake: () => void; canMake: boolean;
}) {
  return (
    <div className="p-3 bg-secondary/40 rounded-lg border border-border">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-foreground">
            {formatTime(start)} - {formatTime(end)}
          </span>
          <span className={`px-1.5 py-0.5 text-xs rounded font-medium ${badgeClass}`}>{badge}</span>
          {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
        </div>
        <button
          onClick={onMake}
          disabled={!canMake}
          title={canMake ? 'Open the capture tool to make a template'
            : 'Original audio not retained for this episode'}
          className={`${makeBtn} shrink-0`}
        >
          Make template
        </button>
      </div>
    </div>
  );
}

function DetectedCuesSection({
  slug, episodeId, episodeTitle, episodeDuration, hasOriginalAudio,
}: DetectedCuesSectionProps) {
  const queryClient = useQueryClient();
  // Shares the CollapsibleSection's persisted open state so a section restored
  // open on mount still enables the lazy queries.
  const [expanded, setExpanded] = useLocalStorageState<boolean>('episode-detected-cues', false);
  const [scanned, setScanned] = useState(false);
  const [seed, setSeed] = useState<{ start: number; end: number } | null>(null);

  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const captureMinSeconds = settingsQuery.data?.audioCueCaptureMinSeconds?.value ?? 0.2;
  const captureMaxSeconds = settingsQuery.data?.audioCueCaptureMaxSeconds?.value ?? 4;

  // Template matches: instant DB read, auto-loaded.
  const matchesQuery = useQuery({
    queryKey: ['detected-cues', slug, episodeId],
    queryFn: () => getDetectedCues(slug, episodeId),
    enabled: expanded,
  });
  // Recurring candidates: decodes the whole episode, so only on an explicit scan.
  const candidatesQuery = useQuery({
    queryKey: ['cue-candidates', slug, episodeId],
    queryFn: () => getCueCandidates(slug, episodeId),
    enabled: expanded && scanned,
    staleTime: Infinity,
  });

  const matches: DetectedCue[] = matchesQuery.data?.detectedCues ?? [];
  const candidates: CueCandidate[] = candidatesQuery.data?.candidates ?? [];

  const makeTemplate = (start: number, end: number) =>
    setSeed({ start, end: Math.min(end, start + captureMaxSeconds) });

  return (
    <CollapsibleSection
      title="Detected Cues"
      subtitle="Find a recurring sound to make a cue template"
      defaultOpen={false}
      storageKey="episode-detected-cues"
      onToggle={setExpanded}
    >
      {matches.length > 0 && (
        <div className="mb-4">
          <h4 className="text-sm font-semibold text-foreground mb-2">Template matches</h4>
          <div className="space-y-2">
            {matches.map((m) => (
              <CueRow
                key={`m:${m.start}`}
                start={m.start}
                end={m.end}
                badge={m.cueType ? cueTypeLabel(m.cueType as CueTemplateType) : 'Template match'}
                badgeClass="bg-violet-500/20 text-violet-600 dark:text-violet-400"
                meta={m.score != null ? `Match ${Math.round(m.score * 100)}%` : undefined}
                onMake={() => makeTemplate(m.start, m.end)}
                canMake={hasOriginalAudio}
              />
            ))}
          </div>
        </div>
      )}

      <p className="text-sm text-muted-foreground mb-3">
        Scan the audio for sounds that repeat across the episode -- the kind worth
        templating. One-off loud moments are skipped.
      </p>

      {!scanned && (
        <button
          onClick={() => setScanned(true)}
          disabled={!hasOriginalAudio}
          title={hasOriginalAudio ? '' : 'Original audio not retained for this episode'}
          className={makeBtn}
        >
          Find cue candidates
        </button>
      )}

      {scanned && candidatesQuery.isLoading && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <LoadingSpinner size="sm" inline className="w-4 h-4" /> Scanning audio, this can take a moment...
        </p>
      )}
      {scanned && candidatesQuery.error && (
        <p className="text-sm text-destructive">Scan failed. Try again.</p>
      )}
      {scanned && candidatesQuery.data && candidates.length === 0 && (
        <p className="text-sm text-muted-foreground">No recurring sounds found.</p>
      )}

      {candidates.length > 0 && (
        <div className="space-y-2">
          {candidates.map((c) => (
            <CueRow
              key={`c:${c.start}`}
              start={c.start}
              end={c.end}
              badge={`Repeats ${c.count}x`}
              badgeClass="bg-blue-500/20 text-blue-600 dark:text-blue-400"
              meta={c.prominenceDb != null ? `${c.prominenceDb.toFixed(1)} dB` : undefined}
              onMake={() => makeTemplate(c.start, c.end)}
              canMake={hasOriginalAudio}
            />
          ))}
        </div>
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
          onFinalSave={() => { setSeed(null); matchesQuery.refetch(); }}
        />
      )}
    </CollapsibleSection>
  );
}

export default DetectedCuesSection;
