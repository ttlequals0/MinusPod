import PatternLink from '../PatternLink';
import { DetectedAd, formatStageName } from './types';

interface ReasonPanelProps {
  selectedAd: DetectedAd;
}

export function ReasonPanel({ selectedAd }: ReasonPanelProps) {
  return (
    <div className="px-3 sm:px-4 py-3 sm:py-4 min-h-0 overflow-y-auto">
      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">Why this was flagged</h4>
      {selectedAd.reason ? (
        <p className="text-sm text-foreground wrap-break-word mb-4">
          <PatternLink reason={selectedAd.reason} />
        </p>
      ) : (
        <p className="text-sm text-muted-foreground italic mb-4">No reason provided</p>
      )}
      <div className="flex flex-wrap gap-4 text-sm">
        <div>
          <span className="text-xs text-muted-foreground">Confidence</span>
          <p className="font-medium">{Math.round(selectedAd.confidence * 100)}%</p>
        </div>
        {selectedAd.detection_stage && (
          <div>
            <span className="text-xs text-muted-foreground">Detection stage</span>
            <p className="font-medium">
              {formatStageName(selectedAd.detection_stage)}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
