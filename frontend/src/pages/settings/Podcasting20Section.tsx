import { useMemo } from 'react';
import type { StageTunables, UpdateSettingsPayload } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import { btnPrimary } from '../../components/buttonStyles';
import SavedBadge from './SavedBadge';
import {
  NumberFieldRow,
  baselineFromTunables,
  parseIntField,
  readEnvOverride,
  useServerDraft,
} from './StageTunablesSection';
import { focusRing } from '../../components/fieldStyles';

const CHAPTER_GEOMETRY_FIELDS = [
  {
    key: 'chapterTargetSeconds', label: 'Target chapter length (seconds)',
    min: 120, max: 3600, step: 30,
    help: '120 to 3600. Default 600 (10 min). Lower means more chapters.',
  },
  {
    key: 'chapterWindowSeconds', label: 'Transcript window (seconds)',
    min: 600, max: 10800, step: 300,
    help: '600 to 10800. Default 2700 (45 min). One LLM call per window.',
  },
  {
    key: 'chapterMaxBoundaries', label: 'Maximum chapters',
    min: 1, max: 200, step: 1,
    help: '1 to 200. Default 40. Was hardcoded to 6 before 2.82.0.',
  },
  {
    key: 'chapterMinDurationSeconds', label: 'Shortest chapter (seconds)',
    min: 30, max: 900, step: 15,
    help: '30 to 900. Default 180 (3 min). Shorter chapters merge into the previous one.',
  },
] as const;

type GeometryKey = (typeof CHAPTER_GEOMETRY_FIELDS)[number]['key'];
const CHAPTER_GEOMETRY_KEYS = CHAPTER_GEOMETRY_FIELDS.map((f) => f.key);

export interface ChapterGeometryProps {
  tunables: StageTunables;
  defaults: Record<keyof StageTunables, number | string | null>;
  onSave: (payload: UpdateSettingsPayload) => void;
  saveIsPending: boolean;
  saveIsSuccess: boolean;
  saveError: string | null;
}

function ChapterGeometryBlock({
  tunables,
  defaults,
  onSave,
  saveIsPending,
  saveIsSuccess,
  saveError,
}: ChapterGeometryProps) {
  const serverBaseline = useMemo(
    () => baselineFromTunables(tunables, CHAPTER_GEOMETRY_KEYS),
    [tunables],
  );
  const { draft, dirty, setField, buildPayload } = useServerDraft(
    CHAPTER_GEOMETRY_KEYS,
    serverBaseline,
  );

  const eff = (key: GeometryKey) => (draft[key] ?? defaults[key]) as number | null;
  const targetEff = eff('chapterTargetSeconds');
  const windowEff = eff('chapterWindowSeconds');
  const minEff = eff('chapterMinDurationSeconds');
  let crossFieldError: string | null = null;
  if (targetEff !== null && windowEff !== null && targetEff > windowEff) {
    crossFieldError = 'Target chapter length must not exceed the transcript window.';
  } else if (minEff !== null && targetEff !== null && minEff > targetEff) {
    crossFieldError = 'Shortest chapter must not exceed the target chapter length.';
  }

  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div>
        <h4 className="text-sm font-semibold text-foreground">Chapter Density</h4>
        <p className="text-xs text-muted-foreground mt-0.5">
          How many chapters a long episode gets, and how much transcript each
          detection call sees. Applies on the next episode.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {CHAPTER_GEOMETRY_FIELDS.map((field) => {
          const env = readEnvOverride(tunables[field.key]);
          return (
            <NumberFieldRow
              key={field.key}
              label={field.label}
              resetDisabled={draft[field.key] === null}
              onReset={() => setField(field.key, null)}
              value={draft[field.key]}
              fallback={defaults[field.key] as number | null}
              min={field.min}
              max={field.max}
              step={field.step}
              parse={parseIntField}
              onChange={(parsed) => setField(field.key, parsed)}
              help={env ? `Default from ${env}.` : field.help}
            />
          );
        })}
      </div>

      {crossFieldError && (
        <p className="text-xs text-destructive">{crossFieldError}</p>
      )}

      <div className="pt-1 flex items-center">
        <button
          type="button"
          onClick={() => onSave(buildPayload())}
          disabled={!dirty || saveIsPending || !!crossFieldError}
          className={`px-4 py-2 rounded-lg ${btnPrimary} disabled:opacity-50 transition-colors text-sm ${focusRing}`}
        >
          {saveIsPending ? 'Saving...' : 'Save Chapter Density'}
        </button>
        {saveIsSuccess && !dirty && !saveError && <SavedBadge className="ml-3" />}
        {saveError && (
          <span className="ml-3 text-sm text-destructive">{saveError}</span>
        )}
      </div>
    </div>
  );
}

interface Podcasting20SectionProps {
  vttTranscriptsEnabled: boolean;
  chaptersEnabled: boolean;
  onVttTranscriptsEnabledChange: (enabled: boolean) => void;
  onChaptersEnabledChange: (enabled: boolean) => void;
  geometry?: ChapterGeometryProps;
}

function Podcasting20Section({
  vttTranscriptsEnabled,
  chaptersEnabled,
  onVttTranscriptsEnabledChange,
  onChaptersEnabledChange,
  geometry,
}: Podcasting20SectionProps) {
  return (
    <CollapsibleSection title="Transcripts & Chapters">
      <div className="space-y-4">
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={vttTranscriptsEnabled}
              onChange={onVttTranscriptsEnabledChange}
              ariaLabel="Generate VTT Transcripts"
            />
            <span className="text-sm font-medium text-foreground">Generate VTT Transcripts</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Create WebVTT transcripts with adjusted timestamps for podcast apps
          </p>
        </div>

        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={chaptersEnabled}
              onChange={onChaptersEnabledChange}
              ariaLabel="Generate Chapters"
            />
            <span className="text-sm font-medium text-foreground">Generate Chapters</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Create JSON chapters from ad boundaries and description timestamps
          </p>
        </div>

        {geometry && <ChapterGeometryBlock {...geometry} />}
      </div>
    </CollapsibleSection>
  );
}

export default Podcasting20Section;
