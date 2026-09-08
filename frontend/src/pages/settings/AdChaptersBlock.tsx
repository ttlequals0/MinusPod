import Checkbox from '../../components/Checkbox';
import NumberInput from '../../components/NumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import { inputBase } from '../../components/fieldStyles';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, type SegmentCategory,
} from '../../utils/segmentCategory';

// Mirrors config.DEFAULT_AD_CHAPTER_CATEGORIES, used until the settings load.
export const DEFAULT_AD_CHAPTER_CATEGORIES = Object.fromEntries(
  SEGMENT_CATEGORIES.map((c) => [c, c === 'sponsor' || c === 'cross_promo']),
) as Record<SegmentCategory, boolean>;

export interface AdChaptersBlockProps {
  chaptersEnabled: boolean;
  enabled: boolean;
  categories: Record<SegmentCategory, boolean>;
  includeHeld: boolean;
  titleFormat: string;
  heldTitleFormat: string;
  resumeTitle: string;
  minConfidence: number;
  onEnabledChange: (v: boolean) => void;
  onCategoryChange: (category: SegmentCategory, checked: boolean) => void;
  onIncludeHeldChange: (v: boolean) => void;
  onTitleFormatChange: (v: string) => void;
  onHeldTitleFormatChange: (v: string) => void;
  onResumeTitleChange: (v: string) => void;
  onMinConfidenceChange: (v: number) => void;
}

function TextField({ id, label, value, disabled, onChange, help }: {
  id: string;
  label: string;
  value: string;
  disabled: boolean;
  onChange: (v: string) => void;
  help?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-foreground mb-2">
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full ${inputBase}`}
      />
      {help && <p className="mt-2 text-sm text-muted-foreground">{help}</p>}
    </div>
  );
}

function AdChaptersBlock({
  chaptersEnabled,
  enabled,
  categories,
  includeHeld,
  titleFormat,
  heldTitleFormat,
  resumeTitle,
  minConfidence,
  onEnabledChange,
  onCategoryChange,
  onIncludeHeldChange,
  onTitleFormatChange,
  onHeldTitleFormatChange,
  onResumeTitleChange,
  onMinConfidenceChange,
}: AdChaptersBlockProps) {
  const disabled = !chaptersEnabled;
  return (
    <div className={`ml-14 space-y-4 ${disabled ? 'opacity-50' : ''}`}>
      <div>
        <label className={`flex items-center gap-3 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
          <ToggleSwitch
            checked={enabled}
            onChange={onEnabledChange}
            disabled={disabled}
            ariaLabel="Ad chapters"
          />
          <span className="text-sm font-medium text-foreground">Ad chapters</span>
        </label>
        <p className="mt-2 text-sm text-muted-foreground">
          Publish segments left in the audio as their own chapters, so apps that
          skip by chapter can jump past them. Needs Generate Chapters.
        </p>
        {disabled && (
          <p className="mt-2 text-sm text-muted-foreground">
            Turn on Generate Chapters to use ad chapters.
          </p>
        )}
      </div>

      {enabled && (
        <>
          <div>
            <p className="text-sm font-medium text-foreground">Chapter these categories</p>
            <p className="mt-2 text-sm text-muted-foreground">
              Only kept segments in these categories get a chapter. Each feed can
              override this list.
            </p>
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2">
              {SEGMENT_CATEGORIES.map((category) => (
                <Checkbox
                  key={category}
                  checked={categories[category] ?? false}
                  disabled={disabled}
                  onChange={(checked) => onCategoryChange(category, checked)}
                  label={SEGMENT_CATEGORY_LABELS[category]}
                />
              ))}
            </div>
          </div>

          <div>
            <label className={`flex items-center gap-3 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
              <ToggleSwitch
                checked={includeHeld}
                onChange={onIncludeHeldChange}
                disabled={disabled}
                ariaLabel="Include segments waiting for review"
              />
              <span className="text-sm font-medium text-foreground">
                Include segments waiting for review
              </span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground">
              Held segments get a chapter too, titled with the waiting-for-review
              format. Rejecting one removes its chapter.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <TextField
              id="adChapterTitleFormat"
              label="Chapter title"
              value={titleFormat}
              disabled={disabled}
              onChange={onTitleFormatChange}
              help="{category} is replaced with the segment category, for example [mp:sponsor]."
            />
            <TextField
              id="adChapterHeldTitleFormat"
              label="Title while waiting for review"
              value={heldTitleFormat}
              disabled={disabled}
              onChange={onHeldTitleFormatChange}
            />
            <TextField
              id="adChapterResumeTitle"
              label="Resume title"
              value={resumeTitle}
              disabled={disabled}
              onChange={onResumeTitleChange}
              help="Marks where the show picks up after a break."
            />
            <div>
              <label htmlFor="adChapterMinConfidence" className="block text-sm font-medium text-foreground mb-2">
                Minimum confidence
              </label>
              <NumberInput
                id="adChapterMinConfidence"
                value={minConfidence}
                min={0}
                max={1}
                step={0.05}
                fallback={0.9}
                disabled={disabled}
                onCommit={onMinConfidenceChange}
              />
              <p className="mt-2 text-sm text-muted-foreground">
                Kept segments below this score get no chapter. 0 to 1, default 0.9.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default AdChaptersBlock;
