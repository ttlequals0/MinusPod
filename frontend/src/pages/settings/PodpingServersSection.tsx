import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react';
import { getErrorMessage } from '../../api/client';
import {
  getPodpingNodes,
  podpingNodesQueryKey,
  resetPodpingNodes,
  updatePodpingNodes,
} from '../../api/podping';
import { btnGhost, btnSecondary, touchTarget } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';

const MAX_NODES = 20;
const fieldInput = `min-h-11 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground ${focusRing}`;
const actionButton = `min-h-11 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`;

function PodpingServersSection({ enabled }: { enabled: boolean }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: podpingNodesQueryKey,
    queryFn: getPodpingNodes,
    staleTime: Infinity,
    enabled,
  });
  const [draft, setDraft] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: updatePodpingNodes,
    onSuccess: async (result) => {
      setDraft(null);
      setError(null);
      queryClient.setQueryData(podpingNodesQueryKey, result);
    },
    onError: (cause: unknown) => setError(getErrorMessage(cause, 'Could not save Podping nodes')),
  });
  const reset = useMutation({
    mutationFn: resetPodpingNodes,
    onSuccess: async (result) => {
      setDraft(null);
      setError(null);
      queryClient.setQueryData(podpingNodesQueryKey, result);
    },
    onError: (cause: unknown) => setError(getErrorMessage(cause, 'Could not reset Podping nodes')),
  });

  if (!enabled) return null;
  if (isLoading) return <p className="mt-3 text-sm text-muted-foreground">Loading Hive RPC nodes...</p>;
  if (isError || !data) {
    return <p className="mt-3 text-sm text-destructive">Could not load Hive RPC nodes.</p>;
  }

  const nodes = draft ?? data.nodes;
  const changed = nodes.length !== data.nodes.length
    || nodes.some((node, index) => node !== data.nodes[index]);
  const busy = save.isPending || reset.isPending;
  const canSave = changed && nodes.length > 0 && nodes.length <= MAX_NODES
    && nodes.every((node) => node.trim().length > 0) && !busy;
  const move = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= nodes.length) return;
    const reordered = [...nodes];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    setDraft(reordered);
  };

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="mt-1 text-sm text-muted-foreground">
            The listener rotates through these endpoints. Observed feed hosts remain separate in System Health.
          </p>
        </div>
        <button
          type="button"
          className={`${actionButton} ${btnSecondary}`}
          disabled={busy || (nodes.every((node, index) => node === data.defaults[index])
            && nodes.length === data.defaults.length)}
          onClick={() => { setError(null); reset.mutate(); }}
        >
          Reset defaults
        </button>
      </div>
      <div className="mt-3 space-y-2">
        {nodes.map((node, index) => (
          <div key={index} className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <label className="sr-only" htmlFor={`podping-node-${index}`}>
              Hive RPC node {index + 1}
            </label>
            <input
              id={`podping-node-${index}`}
              type="url"
              value={node}
              disabled={busy}
              onChange={(event) => setDraft(nodes.map((value, i) => (
                i === index ? event.target.value : value
              )))}
              className={`${fieldInput} min-w-0 sm:flex-1`}
              placeholder="https://api.example.org"
              autoComplete="url"
            />
            <div className="flex justify-end gap-1 sm:shrink-0">
              <button
                type="button"
                className={`${touchTarget} h-8 w-8 rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
                aria-label={`Move Hive RPC node ${index + 1} up`}
                disabled={busy || index === 0}
                onClick={() => move(index, -1)}
              >
                <ArrowUp aria-hidden="true" className="h-4 w-4" />
              </button>
              <button
                type="button"
                className={`${touchTarget} h-8 w-8 rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
                aria-label={`Move Hive RPC node ${index + 1} down`}
                disabled={busy || index === nodes.length - 1}
                onClick={() => move(index, 1)}
              >
                <ArrowDown aria-hidden="true" className="h-4 w-4" />
              </button>
              <button
                type="button"
                className={`${touchTarget} h-8 w-8 rounded ${btnGhost} disabled:opacity-50 transition-colors ${focusRing}`}
                aria-label={`Remove Hive RPC node ${index + 1}`}
                disabled={busy || nodes.length === 1}
                onClick={() => setDraft(nodes.filter((_, i) => i !== index))}
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          className={`${actionButton} ${btnSecondary}`}
          disabled={busy || nodes.length >= MAX_NODES}
          onClick={() => setDraft([...nodes, ''])}
        >
          Add node
        </button>
        <button
          type="button"
          className={`${actionButton} rounded-lg bg-primary px-4 text-primary-foreground`}
          disabled={!canSave}
          onClick={() => { setError(null); save.mutate(nodes); }}
        >
          {save.isPending ? 'Saving...' : 'Save nodes'}
        </button>
        <span className="text-sm text-muted-foreground">1 to {MAX_NODES} unique HTTP(S) URLs</span>
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}

export default PodpingServersSection;
