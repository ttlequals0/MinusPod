import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { episodeOriginalUrl, getSponsorBlockCandidates, submitSponsorBlock } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import type { SbCandidate, SbCandidatesReason } from '../api/types';
import { useAuditionPlayer } from '../hooks/useAuditionPlayer';
import { formatTimestamp } from '../utils/format';
import { AuditionPlayButton } from './AuditionPlayButton';
import { btnBase, btnPrimary, btnSecondary } from './buttonStyles';
import CloseButton from './CloseButton';
import Checkbox from './Checkbox';
import LoadingSpinner from './LoadingSpinner';
import { Modal, dialogFooterBtn } from './Modal';
import { SegmentCategoryBadge } from './SegmentCategoryBadge';
import { cardTitle } from './pageStyles';

const REASON_TEXT: Record<SbCandidatesReason, string> = {
  not_youtube: 'Only episodes from YouTube feeds can be submitted.',
  disabled: 'SponsorBlock submissions are turned off. Enable them in Settings under YouTube.',
  no_user_id: 'Save a SponsorBlock private user ID in Settings under YouTube first.',
  community_marks: 'This video already has community SponsorBlock marks, so there is nothing new to submit.',
  no_video_id: 'This episode has no YouTube video ID to submit against.',
  marks_unverified: 'SponsorBlock has not confirmed yet that this video has no community marks.',
  not_completed: 'Submission opens once processing has completed.',
  service_down: 'SponsorBlock is unreachable right now. Try again later.',
};

const STATUS_TEXT: Record<string, string> = {
  submitted: 'Submitted',
  duplicate: 'Already on SponsorBlock',
  rejected: 'Rejected',
  rate_limited: 'Rate limited',
  error: 'Error',
};

const keyOf = (c: SbCandidate) => `${c.start}-${c.end}`;

function SubmitDialog({ slug, episodeId, hasOriginalAudio, onClose }: {
  slug: string; episodeId: string; hasOriginalAudio: boolean; onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const titleId = useId();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // Candidate times are original-audio times; the hook pauses on unmount.
  const audition = useAuditionPlayer();
  const originalUrl = episodeOriginalUrl(slug, episodeId);

  const { data, isLoading, error: loadError } = useQuery({
    queryKey: ['sb-candidates', slug, episodeId],
    queryFn: () => getSponsorBlockCandidates(slug, episodeId),
    gcTime: 0,
  });

  const submit = useMutation({
    mutationFn: () => submitSponsorBlock(
      slug, episodeId,
      (data?.segments ?? []).filter((c) => picked.has(keyOf(c))).map(({ start, end }) => ({ start, end })),
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['episode', slug, episodeId] });
      onClose();
    },
  });

  const toggle = (c: SbCandidate, on: boolean) => setPicked((prev) => {
    const next = new Set(prev);
    if (on) next.add(keyOf(c)); else next.delete(keyOf(c));
    return next;
  });

  return (
    <Modal
      onClose={onClose}
      closeOnEscape={!submit.isPending}
      ariaLabelledBy={titleId}
      panelClassName="w-full max-w-2xl max-h-[85vh] flex flex-col"
      header={(
        <>
          <div className="min-w-0">
            <h2 id={titleId} className={cardTitle}>Submit to SponsorBlock</h2>
            <p className="text-sm text-muted-foreground">
              Review each segment against the video. Only checked segments are sent.
            </p>
          </div>
          <CloseButton onClick={onClose} />
        </>
      )}
      footer={(
        <>
          <button type="button" className={`${dialogFooterBtn} ${btnSecondary}`} disabled={submit.isPending} onClick={onClose}>
            Cancel
          </button>
          {data?.allowed && (
            <button
              type="button"
              className={`${dialogFooterBtn} ${btnPrimary}`}
              disabled={picked.size === 0 || submit.isPending}
              onClick={() => submit.mutate()}
            >
              {submit.isPending ? 'Submitting...' : `Submit ${picked.size} ${picked.size === 1 ? 'segment' : 'segments'}`}
            </button>
          )}
        </>
      )}
    >
      <div className="p-4 space-y-3">
        {isLoading && <LoadingSpinner />}
        {loadError && <p role="alert" className="text-sm text-destructive">{getErrorMessage(loadError, 'Could not load the segments.')}</p>}
        {data && !data.allowed && (
          <p className="text-sm text-foreground">{data.reason ? REASON_TEXT[data.reason] : 'Submissions are not available for this episode.'}</p>
        )}
        {data?.allowed && data.segments.length === 0 && (
          <p className="text-sm text-muted-foreground">No cut segments qualify for submission.</p>
        )}
        {data?.allowed && data.segments.length > 0 && (
          <ul className="space-y-2">
            {data.segments.map((c) => {
              const key = keyOf(c);
              const done = c.submission?.status === 'submitted' || c.submission?.status === 'duplicate';
              const id = `sb-seg-${key}`;
              const from = formatTimestamp(c.start);
              const to = formatTimestamp(c.end);
              return (
                <li key={key} className="flex items-start gap-3 rounded-lg border border-border p-3">
                  {/* Outside the label so a tap on play never toggles the checkbox. */}
                  <AuditionPlayButton
                    playing={audition.playingKey === key}
                    onClick={() => audition.toggle(key, originalUrl, c.start, c.end)}
                    label={`segment ${from} to ${to}`}
                    disabled={!hasOriginalAudio}
                    title={hasOriginalAudio ? undefined : 'The original audio is no longer kept'}
                  />
                  <label htmlFor={id} className={`flex flex-1 min-w-0 items-start gap-3 min-h-11 ${done ? '' : 'cursor-pointer'}`}>
                    <Checkbox
                      id={id}
                      checked={picked.has(key)}
                      disabled={done || submit.isPending}
                      onChange={(on) => toggle(c, on)}
                      className="mt-0.5 shrink-0"
                    />
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm">{from} - {to}</span>
                        <SegmentCategoryBadge category={c.category} />
                        {c.submission && (
                          <span className="text-xs text-muted-foreground">
                            {STATUS_TEXT[c.submission.status] ?? c.submission.status}
                          </span>
                        )}
                      </span>
                      {c.evidence.length > 0 && (
                        <span className="block text-sm text-muted-foreground break-words">{c.evidence.join(', ')}</span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
        {audition.audioElement}
        {submit.error && <p role="alert" className="text-sm text-destructive">{getErrorMessage(submit.error, 'Could not submit to SponsorBlock.')}</p>}
      </div>
    </Modal>
  );
}

function SponsorBlockSubmit({ slug, episodeId, hasOriginalAudio = true }: {
  slug: string; episodeId: string; hasOriginalAudio?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={`${btnBase} ${btnSecondary}`} onClick={() => setOpen(true)}>
        Submit to SponsorBlock
      </button>
      {open && <SubmitDialog slug={slug} episodeId={episodeId} hasOriginalAudio={hasOriginalAudio} onClose={() => setOpen(false)} />}
    </>
  );
}

export default SponsorBlockSubmit;
