import { type ReactNode, useMemo } from 'react';
import type { StageTunables, UpdateSettingsPayload } from '../../api/types';
import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import SavedBadge from './SavedBadge';
import {
  NumberFieldRow,
  baselineFromTunables,
  parseIntField,
  readEnvOverride,
  useServerDraft,
} from './StageTunablesSection';
import AdChaptersBlock, { type AdChaptersBlockProps } from './AdChaptersBlock';
import { btnLg, btnPrimary } from '../../components/buttonStyles';
import { selectBase, labelBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';
import { DemoDisabled } from '../../components/DemoDisabled';
import { GENERATE_AS_AUTO_NOTE, LlmDisabledNote, LlmGate } from './LlmGate';
import { useLlmEnabled } from '../../hooks/useLlmEnabled';

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
    help: '1 to 200. Default 40.',
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
  const llmEnabled = useLlmEnabled();
  const serverBaseline = useMemo(
    () => baselineFromTunables(tunables, CHAPTER_GEOMETRY_KEYS),
    [tunables],
  );
  const { draft, dirty, setField, buildPayload } = useServerDraft(
    CHAPTER_GEOMETRY_KEYS,
    serverBaseline,
    llmEnabled,
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
    <div className="bg-muted/40 rounded-lg p-3 space-y-3">
      <div>
        <h4 className={subHeading}>Chapter density</h4>
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
        <DemoDisabled>
          <button
            type="button"
            onClick={() => onSave(buildPayload())}
            disabled={!dirty || saveIsPending || !!crossFieldError}
            className={`${btnLg} ${btnPrimary}`}
          >
            {saveIsPending ? 'Saving...' : 'Save chapter density'}
          </button>
        </DemoDisabled>
        {saveIsSuccess && !dirty && !saveError && <SavedBadge className="ml-3" />}
        {saveError && (
          <span className="ml-3 text-sm text-destructive">{saveError}</span>
        )}
      </div>
    </div>
  );
}

export function ToggleRow({ checked, onChange, label, disabled, children }: {
  checked: boolean;
  onChange: (enabled: boolean) => void;
  label: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <div>
      <label className={`flex items-center gap-3 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
        <ToggleSwitch checked={checked} onChange={onChange} disabled={disabled} ariaLabel={label} />
        <span className="text-sm font-medium text-foreground">{label}</span>
      </label>
      <p className="mt-2 text-sm text-muted-foreground ml-14">{children}</p>
    </div>
  );
}

interface Podcasting20SectionProps {
  vttTranscriptsEnabled: boolean;
  chaptersEnabled: boolean;
  chaptersInNotes: boolean;
  reviewLinksInNotes: boolean;
  onVttTranscriptsEnabledChange: (enabled: boolean) => void;
  onChaptersEnabledChange: (enabled: boolean) => void;
  onChaptersInNotesChange: (enabled: boolean) => void;
  onReviewLinksInNotesChange: (enabled: boolean) => void;
  chaptersMode: 'auto' | 'generate' | 'off';
  onChaptersModeChange: (mode: 'auto' | 'generate' | 'off') => void;
  unsupportedChaptersRoute?: string | null;
  adChapters?: AdChaptersBlockProps;
  geometry?: ChapterGeometryProps;
}

function Podcasting20Section({
  vttTranscriptsEnabled,
  chaptersEnabled,
  chaptersInNotes,
  reviewLinksInNotes,
  onVttTranscriptsEnabledChange,
  onChaptersEnabledChange,
  onChaptersInNotesChange,
  onReviewLinksInNotesChange,
  chaptersMode,
  onChaptersModeChange,
  unsupportedChaptersRoute = null,
  adChapters,
  geometry,
}: Podcasting20SectionProps) {
  const llmEnabled = useLlmEnabled();
  return (
    <CollapsibleSection title="Transcripts and chapters" storageKey="settings-section-transcripts-&-chapters">
      <div className="space-y-4 max-sm:[&_select]:min-h-11 max-sm:[&_button]:min-h-11">
        <ToggleRow checked={vttTranscriptsEnabled} onChange={onVttTranscriptsEnabledChange}
          label="Generate VTT Transcripts">
          Create WebVTT transcripts with adjusted timestamps for podcast apps
        </ToggleRow>

        <ToggleRow checked={chaptersEnabled} onChange={onChaptersEnabledChange}
          disabled={!chaptersEnabled && !!unsupportedChaptersRoute}
          label="Generate chapters">
          Create JSON chapters from ad boundaries and description timestamps
        </ToggleRow>
        {unsupportedChaptersRoute && (
          <div role="status" className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
            System One cannot generate chapters. Select a chat provider and model.
          </div>
        )}

        <div className="border-t border-border pt-4">
          <label htmlFor="globalChaptersMode" className={labelBase}>
            Chapter mode for feeds that inherit the global setting
          </label>
          <select id="globalChaptersMode" value={chaptersMode}
            onChange={(e) => onChaptersModeChange(e.target.value as 'auto' | 'generate' | 'off')}
            aria-describedby={llmEnabled ? undefined : 'globalChaptersMode-llm-disabled'}
            className={`w-full ${selectBase}`}>
            <option value="auto">Auto</option>
            <option value="generate" disabled={!llmEnabled}>Always generate</option>
            <option value="off">Off</option>
          </select>
          {!llmEnabled && <LlmDisabledNote id="globalChaptersMode-llm-disabled" className="mt-1" text={chaptersMode === 'generate' ? GENERATE_AS_AUTO_NOTE : undefined} />}
        </div>

        {adChapters && <AdChaptersBlock {...adChapters} />}

        <ToggleRow checked={chaptersInNotes} onChange={onChaptersInNotesChange}
          label="List chapters in episode descriptions">
          Append the chapter list (timestamp and title) to each episode&apos;s
          description in the served feed and on the episode page, for apps
          that only show chapters during playback. Feeds can override this.
        </ToggleRow>

        <ToggleRow checked={reviewLinksInNotes} onChange={onReviewLinksInNotesChange}
          label="Add review links to episode descriptions">
          Links to the episode page and a one-tap 5-star rating, shown to
          anyone reading the feed. Feeds can override this.
        </ToggleRow>

        {geometry && (
          <LlmGate disabled={!llmEnabled} id="chapter-density" className="space-y-2">
            <ChapterGeometryBlock {...geometry} />
          </LlmGate>
        )}
      </div>
    </CollapsibleSection>
  );
}

export default Podcasting20Section;
