import { useState } from 'react';
import { Link } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelProcessing,
  getProcessingEpisodes,
  getProcessingAdmission,
  getSettings,
  setQueuePriority,
  updateSettings,
} from '../api/settings';
import type { ProcessingEpisode } from '../api/settings';
import type { UpdateSettingsPayload } from '../api/types';
import { getProcessingStatus } from '../api/status';
import type { ProcessingStatus } from '../api/status';
import ProcessingQueueSection, { QUEUE_PAGE_SIZE } from './settings/ProcessingQueueSection';
import QueueControlSection from './settings/QueueControlSection';
import { getStageLabel } from '../utils/processingStage';
import { focusRing } from '../components/fieldStyles';
import { touchTarget } from '../components/buttonStyles';
import { tint } from '../components/badgeStyles';
import LoadingSpinner from '../components/LoadingSpinner';
import ProcessingJobProgress from '../components/ProcessingJobProgress';

function ActiveQueue({
  status,
  activeEpisodes,
  onCancel,
  cancelIsPending,
  cancelingKey,
  loading,
}: {
  status: ProcessingStatus | undefined;
  activeEpisodes: ProcessingEpisode[];
  onCancel: (params: { slug: string; episodeId: string }) => void;
  cancelIsPending: boolean;
  cancelingKey: string | null;
  loading: boolean;
}) {
  const jobs = status?.jobs ?? (status?.currentJob ? [status.currentJob] : []);
  const hold = status?.hold;
  return (
    <section className="bg-card rounded-lg border border-border p-4 sm:p-6" aria-labelledby="active-processing-title">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h2 id="active-processing-title" className="text-lg font-semibold text-foreground">In progress</h2>
          <p className="text-sm text-muted-foreground mt-0.5">Episodes currently moving through the pipeline.</p>
        </div>
        {status && status.queueLength > 0 && (
          <span className={`${tint.primary} px-2 py-0.5 text-xs rounded font-medium shrink-0`}>
            {status.queueLength} queued
          </span>
        )}
      </div>
      {loading ? <LoadingSpinner size="sm" /> : jobs.length > 0 ? (
        <div className="space-y-3">
          {jobs.map((job) => (
            <ProcessingJobProgress
              key={`${job.slug}:${job.episodeId}`}
              job={job}
              elapsed={job.elapsed}
              linkTo={`/feeds/${job.slug}/episodes/${job.episodeId}`}
              className="rounded-lg bg-secondary/50 p-4"
              actions={(
                <button
                  type="button"
                  onClick={() => onCancel({ slug: job.slug, episodeId: job.episodeId })}
                  disabled={cancelIsPending}
                  className={`${touchTarget} px-3 py-1 text-sm rounded ${cancelIsPending && cancelingKey === `${job.slug}:${job.episodeId}` ? 'bg-destructive text-destructive-foreground' : 'bg-destructive/20 text-destructive'} disabled:opacity-50 ${focusRing}`}
                >
                  {cancelIsPending && cancelingKey === `${job.slug}:${job.episodeId}` ? 'Canceling...' : 'Cancel'}
                </button>
              )}
            />
          ))}
        </div>
      ) : activeEpisodes.length > 0 ? (
        <div className="space-y-2">
          {activeEpisodes.map((episode) => (
            <div key={`${episode.slug}:${episode.episodeId}`} className="rounded-lg bg-secondary/50 p-4 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <Link
                  to={`/feeds/${episode.slug}/episodes/${episode.episodeId}`}
                  className={`block font-medium text-primary hover:underline truncate ${focusRing}`}
                >
                  {episode.title}
                </Link>
                <p className="text-sm text-muted-foreground truncate">{episode.podcast}{episode.stage ? `, ${getStageLabel(episode.stage)}` : ''}</p>
              </div>
              <button
                type="button"
                onClick={() => onCancel({ slug: episode.slug, episodeId: episode.episodeId })}
                disabled={cancelIsPending}
                className={`${touchTarget} px-3 py-1 text-sm rounded bg-destructive/20 text-destructive disabled:opacity-50 ${focusRing}`}
              >
                {cancelIsPending && cancelingKey === `${episode.slug}:${episode.episodeId}` ? 'Canceling...' : 'Cancel'}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No episodes are processing right now.</p>
      )}
      {hold && (hold.queuePaused || hold.offlineHeld > 0) && (
        <div className="mt-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">
          <p className="font-medium text-warning">Queue hold</p>
          {hold.queuePaused && <p className="mt-1">New work is waiting for the provider limit to reset.</p>}
          {hold.offlineServices.map((service) => (
            <p key={service.service} className="mt-1">
              {service.service}: {service.reachable === false ? 'unreachable' : service.reachable === true ? 'reachable' : 'not checked yet'}, {service.held} waiting
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function QueuePage() {
  const queryClient = useQueryClient();
  const [queuePage, setQueuePage] = useState(1);
  const episodes = useQuery({
    queryKey: ['processing-episodes', queuePage],
    queryFn: () => getProcessingEpisodes({
      queueOffset: (queuePage - 1) * QUEUE_PAGE_SIZE,
      queueLimit: QUEUE_PAGE_SIZE,
    }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  });
  const status = useQuery({
    queryKey: ['processing-status'],
    queryFn: getProcessingStatus,
    enabled: false,
  });
  const settings = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const admission = useQuery({ queryKey: ['processing-admission'], queryFn: getProcessingAdmission });
  const cancel = useMutation({
    mutationFn: (params: { slug: string; episodeId: string }) => cancelProcessing(params.slug, params.episodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['processing-episodes'] });
      queryClient.invalidateQueries({ queryKey: ['processing-status'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });
  const priority = useMutation({
    mutationFn: ({ slug, episodeId, ...change }: { slug: string; episodeId: string; priority?: number; delta?: number }) =>
      setQueuePriority(slug, episodeId, change),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['processing-episodes'] }),
  });
  const tunable = useMutation({
    mutationFn: (payload: UpdateSettingsPayload) => updateSettings(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });
  const configured = settings.data;

  return (
    <div className="max-w-7xl mx-auto space-y-4 pb-20">
      <div>
        <h1 className="text-2xl font-bold text-foreground mb-2">Queue</h1>
        <p className="text-muted-foreground">Monitor active processing and manage queued episodes.</p>
      </div>

      <ActiveQueue
        status={status.data}
        activeEpisodes={(episodes.data ?? []).filter((episode) => episode.stage !== 'queued')}
        onCancel={(params) => cancel.mutate(params)}
        cancelIsPending={cancel.isPending}
        cancelingKey={cancel.variables ? `${cancel.variables.slug}:${cancel.variables.episodeId}` : null}
        loading={status.isPending && episodes.isPending}
      />
      {cancel.isError && <p className="text-sm text-destructive">Could not cancel the selected episode. Refresh and try again.</p>}
      {priority.isError && <p className="text-sm text-destructive">Could not update queue priority. Refresh and try again.</p>}

      {settings.isPending && <LoadingSpinner size="sm" />}
      {settings.isError && <p className="text-sm text-destructive">Could not load queue controls.</p>}
      {configured && <QueueControlSection
        storageKey="queue-page-control"
        processNewEpisodesFirst={configured?.processNewEpisodesFirst?.value ?? configured?.defaults.processNewEpisodesFirst ?? true}
        onProcessNewEpisodesFirstChange={(value) => tunable.mutate({ processNewEpisodesFirst: value })}
        queueManualBoost={configured?.queueManualBoost?.value ?? 20}
        onQueueManualBoostChange={(value) => tunable.mutate({ queueManualBoost: value })}
        queueFreshBoost={configured?.queueFreshBoost?.value ?? 5}
        onQueueFreshBoostChange={(value) => tunable.mutate({ queueFreshBoost: value })}
        queueBulkBoost={configured?.queueBulkBoost?.value ?? 0}
        onQueueBulkBoostChange={(value) => tunable.mutate({ queueBulkBoost: value })}
      />}
      {tunable.isError && <p className="text-sm text-destructive">Could not save queue controls. Try again.</p>}

      {episodes.isPending && <LoadingSpinner size="sm" />}
      {episodes.isError && <p className="text-sm text-destructive">Could not load queued episodes.</p>}
      {episodes.data && <ProcessingQueueSection
        processingEpisodes={episodes.data}
        showActive={false}
        defaultOpen
        storageKey="queue-page-episodes"
        onCancel={(params) => cancel.mutate(params)}
        cancelIsPending={cancel.isPending}
        cancelingKey={cancel.variables ? `${cancel.variables.slug}:${cancel.variables.episodeId}` : null}
        queuePage={queuePage}
        onQueuePage={setQueuePage}
        onPriorityChange={(params) => priority.mutate(params)}
        priorityIsPending={priority.isPending}
      />}
      {admission.data?.paused && (
        <p className="text-sm text-muted-foreground">
          New work is paused. <Link to="/settings" className={`text-primary hover:underline ${focusRing}`}>Open Settings</Link> to change other processing defaults.
        </p>
      )}
    </div>
  );
}

export default QueuePage;
