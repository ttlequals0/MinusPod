import { Link } from 'react-router';
import NumberInput from '../../components/NumberInput';
import { inputBase, focusRing } from '../../components/fieldStyles';
import { ToggleRow } from './Podcasting20Section';

export interface AdChaptersBlockProps {
  chaptersEnabled: boolean;
  includeHeld: boolean;
  titleFormat: string;
  heldTitleFormat: string;
  resumeTitle: string;
  minConfidence: number;
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
  includeHeld,
  titleFormat,
  heldTitleFormat,
  resumeTitle,
  minConfidence,
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
        <p className="text-sm font-medium text-foreground">Ad chapters</p>
        <p className="mt-2 text-sm text-muted-foreground">
          Publish segments left in the audio as their own chapters, so apps that
          skip by chapter can jump past them. Needs Generate Chapters.
        </p>
        {disabled && (
          <p className="mt-2 text-sm text-muted-foreground">
            Turn on Generate Chapters to use ad chapters.
          </p>
        )}
        <p className="mt-2 text-sm text-muted-foreground">
          Set a category&apos;s action to Mark in{' '}
          <Link to="#segment-actions" className={`text-primary hover:underline ${focusRing}`}>
            Segment actions
          </Link>{' '}
          to chapter it.
        </p>
      </div>

      <ToggleRow
        checked={includeHeld}
        onChange={onIncludeHeldChange}
        disabled={disabled}
        label="Include segments waiting for review"
      >
        Held segments get a chapter too, titled with the waiting-for-review
        format. Rejecting one removes its chapter.
      </ToggleRow>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <TextField
          id="adChapterTitleFormat"
          label="Chapter title"
          value={titleFormat}
          disabled={disabled}
          onChange={onTitleFormatChange}
          help="{label} is the category name, such as Sponsor. {category} is its id, such as sponsor."
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
    </div>
  );
}

export default AdChaptersBlock;
