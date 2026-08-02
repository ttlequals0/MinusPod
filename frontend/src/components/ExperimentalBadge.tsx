// Marks a control that can cut audio on evidence the LLM never reviewed.
// Amber matches the warning tone used elsewhere for cue health.
export function ExperimentalBadge({ title }: { title?: string }) {
  return (
    <span
      className="px-2 py-0.5 text-xs rounded font-medium bg-amber-500/20 text-warning align-middle"
      title={title || 'Experimental: cuts here come from audio cues alone'}
    >
      Experimental
    </span>
  );
}

export default ExperimentalBadge;
