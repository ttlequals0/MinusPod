import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection, {
  useCollapsibleOpen, useSectionVisible,
} from '../../components/CollapsibleSection';
import { getNormalizations, deleteNormalization } from '../../api/sponsors';
import { SponsorNormalization } from '../../api/types';
import NormalizationEditModal from '../../components/NormalizationEditModal';
import { ConfirmModal } from '../../components/Modal';
import LoadingSpinner from '../../components/LoadingSpinner';
import { btnOutline, btnPrimary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';

const STORAGE_KEY = 'settings-section-transcript-normalization';

function TranscriptNormalizationSection() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<SponsorNormalization | null | undefined>(undefined);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  // The rule list is unbounded, so fetch it only while the section is on
  // screen rather than on every Settings page load.
  const [open, setOpen] = useCollapsibleOpen(STORAGE_KEY);
  const visible = useSectionVisible(STORAGE_KEY, open);

  const { data: norms, isLoading, error } = useQuery({
    queryKey: ['normalizations'],
    queryFn: getNormalizations,
    enabled: visible,
  });

  const del = useMutation({
    mutationFn: (id: number) => deleteNormalization(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['normalizations'] });
      setDeleteId(null);
    },
  });

  return (
    <CollapsibleSection
      title="Transcript Normalization"
      subtitle="Corrections applied to the transcript before ad detection runs."
      storageKey={STORAGE_KEY}
      onToggle={setOpen}
    >
      <p className="text-sm text-muted-foreground mb-4">
        Fixes misheard words, recurring phrases, numbers, sponsor names, and
        URLs. Sponsor matching sees the canonical spelling, so a name
        transcribed three different ways still matches one pattern.
      </p>
      {isLoading || !visible ? (
        <LoadingSpinner className="py-12" />
      ) : error ? (
        <div className="text-center py-12"><p className="text-destructive">Failed to load normalizations</p></div>
      ) : (
        <div>
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm text-muted-foreground">{norms?.length || 0} rules</div>
            <button
              type="button"
              onClick={() => setEditing(null)}
              className={`px-3 py-1.5 text-sm rounded ${btnPrimary} transition-colors ${focusRing}`}
            >
              + Add Normalization
            </button>
          </div>

          {/* Mobile cards */}
          <div className="sm:hidden space-y-3">
            {norms?.map((n) => (
              <div key={n.id} className="bg-card rounded-lg border border-border p-4">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <span className="text-sm font-mono text-foreground break-all">{n.terms}</span>
                  <span className="shrink-0 px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground">{n.category}</span>
                </div>
                <div className="text-sm text-foreground mb-3 break-all">
                  <span className="text-muted-foreground">→ </span>{n.canonical}
                </div>
                <div className="flex gap-2">
                  <button onClick={() => setEditing(n)} className={`px-2 py-1 text-xs rounded ${btnOutline} ${focusRing}`}>Edit</button>
                  <button onClick={() => setDeleteId(n.id)} className={`px-2 py-1 text-xs rounded border border-destructive/40 text-destructive hover:bg-destructive/10 ${focusRing}`}>Delete</button>
                </div>
              </div>
            ))}
            {norms?.length === 0 && (
              <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">No normalizations</div>
            )}
          </div>

          {/* Desktop table */}
          <div className="hidden sm:block bg-card rounded-lg border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full table-fixed divide-y divide-border">
                <colgroup>
                  <col className="w-[34%]" />
                  <col className="w-[30%]" />
                  <col className="w-[14%]" />
                  <col className="w-[22%]" />
                </colgroup>
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Pattern</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Replacement</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Category</th>
                    <th className="px-2 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {norms?.map((n) => (
                    <tr key={n.id} className="hover:bg-accent/50 transition-colors">
                      <td className="px-4 py-3 overflow-hidden"><span className="text-sm font-mono text-foreground truncate block">{n.terms}</span></td>
                      <td className="px-4 py-3 overflow-hidden"><span className="text-sm text-foreground truncate block">{n.canonical}</span></td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground">{n.category}</span>
                      </td>
                      <td className="px-2 py-3 whitespace-nowrap text-xs">
                        <div className="flex gap-1">
                          <button onClick={() => setEditing(n)} className={`px-2 py-1 rounded ${btnOutline} ${focusRing}`}>Edit</button>
                          <button onClick={() => setDeleteId(n.id)} className={`px-2 py-1 rounded border border-destructive/40 text-destructive hover:bg-destructive/10 ${focusRing}`}>Delete</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {norms?.length === 0 && (
                    <tr><td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">No normalizations</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {editing !== undefined && (
            <NormalizationEditModal
              normalization={editing}
              onClose={() => setEditing(undefined)}
              onSaved={() => setEditing(undefined)}
            />
          )}

          {deleteId !== null && (
            <ConfirmModal
              title="Delete normalization?"
              pending={del.isPending}
              onCancel={() => setDeleteId(null)}
              onConfirm={() => del.mutate(deleteId)}
            >
              <p className="text-muted-foreground">This permanently removes the rule.</p>
            </ConfirmModal>
          )}
        </div>
      )}
    </CollapsibleSection>
  );
}

export default TranscriptNormalizationSection;
