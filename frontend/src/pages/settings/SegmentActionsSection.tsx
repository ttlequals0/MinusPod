import CollapsibleSection from '../../components/CollapsibleSection';
import ToggleSwitch from '../../components/ToggleSwitch';
import SegmentActionToggle from '../../components/SegmentActionToggle';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, SEGMENT_CATEGORY_DESCRIPTIONS, DEFAULT_SEGMENT_ACTION,
  type SegmentCategory, type SegmentAction,
} from '../../utils/segmentCategory';

interface SegmentActionsSectionProps {
  segmentCategoryActions: Partial<Record<SegmentCategory, SegmentAction>>;
  onSegmentCategoryActionChange: (category: SegmentCategory, action: SegmentAction) => void;
  detectShowSegments: boolean;
  onDetectShowSegmentsChange: (enabled: boolean) => void;
}

function SegmentActionsSection({
  segmentCategoryActions,
  onSegmentCategoryActionChange,
  detectShowSegments,
  onDetectShowSegmentsChange,
}: SegmentActionsSectionProps) {
  return (
    <CollapsibleSection
      title="Segment actions"
      subtitle="What happens to each kind of detected segment, and whether to look for show-structure segments at all."
      storageKey="settings-section-segment-actions"
    >
      <div className="space-y-6">
        <div>
          <p className="text-sm text-muted-foreground">
            Choose what happens to each kind of detected segment. Remove cuts it out, Beep replaces it with a tone, Keep leaves it in.
          </p>
          <div className="mt-3 space-y-2">
            {SEGMENT_CATEGORIES.map((category) => (
              <div key={category} className="flex items-center justify-between gap-3 flex-wrap">
                <div className="min-w-0">
                  <span className="text-sm text-foreground block">{SEGMENT_CATEGORY_LABELS[category]}</span>
                  <span className="text-xs text-muted-foreground block">{SEGMENT_CATEGORY_DESCRIPTIONS[category]}</span>
                </div>
                <SegmentActionToggle
                  value={segmentCategoryActions[category] ?? DEFAULT_SEGMENT_ACTION}
                  onChange={(action) => onSegmentCategoryActionChange(category, action)}
                  ariaLabel={`${SEGMENT_CATEGORY_LABELS[category]} action`}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Global default for show-segment (intro/outro/housekeeping) detection. */}
        <div className="pt-4 border-t border-border">
          <label className="flex items-center gap-3 cursor-pointer">
            <ToggleSwitch
              checked={detectShowSegments}
              onChange={onDetectShowSegmentsChange}
              ariaLabel="Detect intro, outro, and housekeeping segments"
            />
            <span className="text-sm font-medium text-foreground">
              Detect intro, outro, and housekeeping segments
            </span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground">
            Applies to feeds that have not set their own value.
          </p>
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default SegmentActionsSection;
