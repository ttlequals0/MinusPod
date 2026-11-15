import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ToggleSwitch from '../../components/ToggleSwitch';
import { getErrorMessage } from '../../api/client';
import {
  getCommunitySegmentsSummary,
  removeAllCommunitySegments,
  setCommunitySegmentsEnabled,
} from '../../api/communitySegments';
import { btnBase, btnDestructive, btnDestructiveOutline, btnSecondary } from '../../components/buttonStyles';
import { subHeading } from '../../components/pageStyles';
import { DemoDisabled } from '../../components/DemoDisabled';

const STATE_ORDER = ['candidate', 'validated', 'adopted', 'dormant', 'suspended', 'unbound', 'revoked'];

// The one switch of show-bound community segments, their counts and "Remove all". Hidden when the
// server does not offer the feature.
function ShowSegmentsPanel() {
  const qc = useQueryClient();
  const { data, isError } = useQuery({
    queryKey: ['communitySegments'],
    queryFn: getCommunitySegmentsSummary,
    retry: false,
  });
  const [confirm, setConfirm] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ['communitySegments'] });

  const toggle = useMutation({
    mutationFn: setCommunitySegmentsEnabled,
    onSuccess: refresh,
    onError: (e: unknown) => setMessage(getErrorMessage(e, 'Could not save the setting.')),
  });
  const removeAll = useMutation({
    mutationFn: removeAllCommunitySegments,
    onSuccess: (res) => {
      setMessage(`Removed ${res.deleted} show segment${res.deleted === 1 ? '' : 's'}.`);
      setConfirm(false);
      refresh();
    },
    onError: (e: unknown) => setMessage(getErrorMessage(e, 'Could not remove show segments.')),
  });

  if (isError || !data) return null;
  const counts = STATE_ORDER.filter((s) => data.counts[s]).map((s) => `${data.counts[s]} ${s}`);

  return (
    <div className="pt-3 border-t border-border space-y-2" data-testid="show-segments-panel">
      <h4 className={subHeading}>Show segments</h4>
      <label className="flex items-center gap-3 cursor-pointer">
        <ToggleSwitch
          checked={data.enabled}
          onChange={(v) => toggle.mutate(v)}
          disabled={toggle.isPending}
          ariaLabel="Include show-bound segments"
        />
        <span className="text-sm font-medium text-foreground">
          Include show-bound segments (host asks and nudges, self-promo, show promos, intros, outros, recaps; never sponsors)
        </span>
      </label>
      <p className="text-sm text-muted-foreground">
        Entries carry no text and only bind to a feed whose own episodes match them. A new entry never cuts on
        its own: it corrects a detection or raises a held suggestion until you confirm it. Needs community
        pattern sync on.
      </p>
      <p className="text-sm text-muted-foreground">
        {counts.length ? `Entries: ${counts.join(', ')}.` : 'No show segments yet.'}
      </p>
      {confirm ? (
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => removeAll.mutate()} disabled={removeAll.isPending}
            className={`${btnBase} ${btnDestructive}`}>
            {removeAll.isPending ? 'Removing...' : 'Remove all show segments'}
          </button>
          <button type="button" onClick={() => setConfirm(false)} className={`${btnBase} ${btnSecondary}`}>
            Cancel
          </button>
        </div>
      ) : (
        <DemoDisabled>
          <button type="button" onClick={() => { setMessage(null); setConfirm(true); }}
            className={`${btnBase} ${btnDestructiveOutline}`}>
            Remove all show segments
          </button>
        </DemoDisabled>
      )}
      {message && <p className="text-sm text-muted-foreground">{message}</p>}
    </div>
  );
}

export default ShowSegmentsPanel;
