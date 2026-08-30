import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getSponsors, deleteSponsor } from '../api/sponsors';
import { getTagVocabulary } from '../api/community';
import { Sponsor } from '../api/types';
import SponsorEditModal from '../components/SponsorEditModal';
import { ConfirmModal } from '../components/Modal';
import { TagChips } from '../components/TagChips';
import LoadingSpinner from '../components/LoadingSpinner';
import { Pagination } from '../components/Pagination';
import { SortHeader, useSortState } from '../components/SortHeader';
import { formatDate } from '../utils/format';
import { btnOutline, btnPrimary } from '../components/buttonStyles';
import Checkbox from '../components/Checkbox';
import { selectBase } from '../components/fieldStyles';
import { focusRing } from '../components/fieldStyles';

type SortField = 'name' | 'category' | 'pattern_count' | 'created_at' | 'last_matched_at';

function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={`px-2 py-0.5 text-xs rounded ${
        active
          ? 'bg-success/20 text-success'
          : 'bg-destructive/20 text-destructive'
      }`}
    >
      {active ? 'Active' : 'Inactive'}
    </span>
  );
}

function SponsorsPage() {
  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Sponsors</h1>
      </div>

      <SponsorsSection />
    </div>
  );
}

function SponsorsSection() {
  const queryClient = useQueryClient();
  const [tagFilter, setTagFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const [page, setPage] = useState(1);
  const { sortField, sortDirection, handleSort } =
    useSortState<SortField>('name', 'asc', () => setPage(1));
  const [editing, setEditing] = useState<Sponsor | null | undefined>(undefined); // undefined = closed, null = new
  const [deleteTarget, setDeleteTarget] = useState<Sponsor | null>(null);
  const limit = 20;

  const { data: sponsors, isLoading, error } = useQuery({
    queryKey: ['sponsors', showInactive],
    queryFn: () => getSponsors(showInactive),
  });

  const { data: vocab } = useQuery({ queryKey: ['tagVocabulary'], queryFn: getTagVocabulary });

  const del = useMutation({
    mutationFn: (id: number) => deleteSponsor(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
      setDeleteTarget(null);
    },
  });

  const filtered = sponsors?.filter((s) => {
    if (tagFilter !== 'all' && !s.tags.includes(tagFilter)) return false;
    if (search) {
      const q = search.toLowerCase();
      return s.name.toLowerCase().includes(q)
        || s.aliases.some((a) => a.toLowerCase().includes(q))
        || (s.category ?? '').toLowerCase().includes(q);
    }
    return true;
  });

  const sorted = filtered?.slice().sort((a, b) => {
    const av = a[sortField];
    const bv = b[sortField];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    let cmp: number;
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    return sortDirection === 'asc' ? cmp : -cmp;
  });

  const totalPages = Math.ceil((sorted?.length || 0) / limit);
  const paginated = sorted?.slice((page - 1) * limit, page * limit);

  if (isLoading) return <LoadingSpinner className="py-12" />;
  if (error) return <div className="text-center py-12"><p className="text-destructive">Failed to load sponsors</p></div>;

  return (
    <div>
      {/* Filters + add */}
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <div className="flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">Tag:</label>
            <select
              value={tagFilter}
              onChange={(e) => { setTagFilter(e.target.value); setPage(1); }}
              className={`${selectBase}`}
            >
              <option value="all">All</option>
              {(vocab?.all_tags ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              placeholder="Search by name, alias, category..."
              className="w-full px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            />
          </div>
          <Checkbox
            checked={showInactive}
            onChange={(v) => { setShowInactive(v); setPage(1); }}
            label="Show inactive"
            labelClassName="text-sm text-muted-foreground"
          />
          <button
            type="button"
            onClick={() => setEditing(null)}
            className={`px-3 py-1.5 text-sm rounded ${btnPrimary} transition-colors ${focusRing}`}
          >
            + Add Sponsor
          </button>
        </div>
      </div>

      <div className="text-sm text-muted-foreground mb-3">{sorted?.length || 0} sponsors</div>

      {/* Mobile cards */}
      <div className="sm:hidden space-y-3 mb-4">
        {paginated?.map((s) => (
          <div key={s.id} className="bg-card rounded-lg border border-border p-4">
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="text-sm font-medium text-foreground">{s.name}</div>
              <StatusBadge active={s.is_active} />
            </div>
            {s.aliases.length > 0 && (
              <div className="text-xs text-muted-foreground mb-1 truncate">{s.aliases.join(', ')}</div>
            )}
            <TagChips tags={s.tags} className="mb-2" />
            <div className="flex items-center gap-4 text-xs text-muted-foreground mb-3">
              <span>{s.pattern_count} patterns</span>
              <span>matched {formatDate(s.last_matched_at)}</span>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setEditing(s)} className={`px-2 py-1 text-xs rounded ${btnOutline} ${focusRing}`}>Edit</button>
              <button onClick={() => setDeleteTarget(s)} className={`px-2 py-1 text-xs rounded border border-destructive/40 text-destructive hover:bg-destructive/10 ${focusRing}`}>Delete</button>
            </div>
          </div>
        ))}
        {paginated?.length === 0 && (
          <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">No sponsors found</div>
        )}
      </div>

      {/* Desktop table */}
      <div className="hidden sm:block bg-card rounded-lg border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-border">
            <colgroup>
              <col className="w-[16%]" />
              <col className="w-[15%]" />
              <col className="w-[11%]" />
              <col className="w-[15%]" />
              <col className="w-[9%]" />
              <col className="w-[10%]" />
              <col className="w-[10%]" />
              <col className="w-[14%]" />
            </colgroup>
            <thead className="bg-muted/50">
              <tr>
                <SortHeader field="name" label="Name" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Aliases</th>
                <SortHeader field="category" label="Category" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Tags</th>
                <SortHeader field="pattern_count" label="Patterns" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="created_at" label="Created" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="last_matched_at" label="Last Matched" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className="px-2 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {paginated?.map((s) => (
                <tr key={s.id} className="hover:bg-accent/50 transition-colors">
                  <td className="px-4 py-3 overflow-hidden">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground truncate">{s.name}</span>
                      {!s.is_active && <StatusBadge active={false} />}
                    </div>
                  </td>
                  <td className="px-4 py-3 overflow-hidden">
                    <div className="text-xs text-muted-foreground truncate">{s.aliases.join(', ') || '-'}</div>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground truncate">{s.category || '-'}</td>
                  <td className="px-4 py-3 overflow-hidden"><TagChips tags={s.tags} /></td>
                  <td className="px-2 py-3 whitespace-nowrap text-sm text-foreground">{s.pattern_count}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground">{formatDate(s.created_at)}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground">{formatDate(s.last_matched_at)}</td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">
                    <div className="flex gap-1">
                      <button onClick={() => setEditing(s)} className={`px-2 py-1 rounded ${btnOutline} ${focusRing}`}>Edit</button>
                      <button onClick={() => setDeleteTarget(s)} className={`px-2 py-1 rounded border border-destructive/40 text-destructive hover:bg-destructive/10 ${focusRing}`}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
              {paginated?.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">No sponsors found</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <Pagination page={page} totalPages={totalPages} total={sorted?.length || 0} onPage={setPage} />

      {editing !== undefined && (
        <SponsorEditModal
          sponsor={editing}
          onClose={() => setEditing(undefined)}
          onSaved={() => setEditing(undefined)}
        />
      )}

      {deleteTarget && (
        <ConfirmModal
          title={`Delete ${deleteTarget.name}?`}
          pending={del.isPending}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => del.mutate(deleteTarget.id)}
        >
          <p>This permanently removes the sponsor.</p>
          {deleteTarget.pattern_count > 0 && (
            <p className="text-warning">
              {deleteTarget.pattern_count} linked ad pattern
              {deleteTarget.pattern_count === 1 ? '' : 's'} will be unlinked (kept, not deleted).
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Note: if this name is detected again, or it is part of the seeded list, it can reappear.
          </p>
        </ConfirmModal>
      )}
    </div>
  );
}

export default SponsorsPage;
