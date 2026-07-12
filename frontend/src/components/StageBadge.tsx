import { DETECTION_STAGE_META, type DetectionStage } from '../utils/detectionStage';

// Detection-stage pill shared by the episode page's marker rows and the
// Ad Review tab. Unknown stages fall back to the raw value with neutral
// styling so a new backend stage never renders as an empty badge.
export function StageBadge({ stage }: { stage: string }) {
  const meta = DETECTION_STAGE_META[stage as DetectionStage];
  return (
    <span className={`px-1.5 py-0.5 text-xs rounded font-medium whitespace-nowrap ${meta?.className ?? 'bg-secondary text-muted-foreground'}`}>
      {meta?.label ?? stage}
    </span>
  );
}
