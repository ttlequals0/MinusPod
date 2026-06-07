import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getSponsors, deleteSponsor,
  getNormalizations, deleteNormalization,
} from '../api/sponsors';
import { getTagVocabulary } from '../api/community';
import { Sponsor, SponsorNormalization } from '../api/types';
import SponsorEditModal from '../components/SponsorEditModal';
import NormalizationEditModal from '../components/NormalizationEditModal';
import DeleteConfirmModal from '../components/DeleteConfirmModal';
import { TagChips } from '../components/TagChips';
import LoadingSpinner from '../components/LoadingSpinner';

type Tab = 'sponsors' | 'normalizations';
type SortField = 'name' | 'category' | 'pattern_count' | 'created_at' | 'last_matched_at';
type SortDirection = 'asc' | 'desc';

const formatDate = (s: string | null) => (s ? new Date(s).toLocaleDateString() : '-');

function StatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={`px-2 py-0.5 text-xs rounded ${
        active
          ? 'bg-green-500/20 text-green-600 dark:text-green-400'
          : 'bg-red-500/20 text-red-600 dark:text-red-400'
      }`}
    >
      {active ? 'Active' : 'Inactive'}
    </span>
  );
}

function SortHeader({
  field, label, className, sortField, sortDirection, onSort,
}: {
  field: SortField;
  label: string;
  className?: string;
  sortField: SortField;
  sortDirection: SortDirection;
  onSort: (f: SortField) => void;
}) {
  return (
    <th
      className={`py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:bg-accent/50 ${className || 'px-4'}`}
      onClick={() => onSort(field)}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && <span>{sortDirection === 'asc' ? '↑' : '↓'}</span>}
      </div>
    </th>
  );
}

function SponsorsPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>('sponsors');

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Sponsors</h1>
        <div className="inline-flex rounded-lg border border-border overflow-hidden text-sm">
          <button
            type="button"
            onClick={() => setTab('sponsors')}
            className={`px-4 py-1.5 transition-colors ${
              tab === 'sponsors'
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:bg-accent'
            }`}
          >
            Sponsors
          </button>
          <button
            type="button"
            onClick={() => setTab('normalizations')}
            className={`px-4 py-1.5 border-l border-border transition-colors ${
              tab === 'normalizations'
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:bg-accent'
            }`}
          >
            Normalizations
          </button>
        </div>
      </div>

      {tab === 'sponsors'
        ? <SponsorsSection queryClient={queryClient} />
        : <NormalizationsSection />}
    </div>
  );
}

function SponsorsSection({ queryClient }: { queryClient: ReturnType<typeof useQueryClient> }) {
  const [tagFilter, setTagFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [page, setPage] = useState(1);
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

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    else { setSortField(field); setSortDirection('asc'); }
    setPage(1);
  };

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
              className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
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
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => { setShowInactive(e.target.checked); setPage(1); }}
              className="rounded"
            />
            <span className="text-sm text-muted-foreground">Show inactive</span>
          </label>
          <button
            type="button"
            onClick={() => setEditing(null)}
            className="px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
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
              <button onClick={() => setEditing(s)} className="px-2 py-1 text-xs rounded border border-border hover:bg-accent">Edit</button>
              <button onClick={() => setDeleteTarget(s)} className="px-2 py-1 text-xs rounded border border-destructive/40 text-destructive hover:bg-destructive/10">Delete</button>
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
                      <button onClick={() => setEditing(s)} className="px-2 py-1 rounded border border-border hover:bg-accent">Edit</button>
                      <button onClick={() => setDeleteTarget(s)} className="px-2 py-1 rounded border border-destructive/40 text-destructive hover:bg-destructive/10">Delete</button>
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
        <DeleteConfirmModal
          title={`Delete ${deleteTarget.name}?`}
          pending={del.isPending}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => del.mutate(deleteTarget.id)}
        >
          <p>This permanently removes the sponsor.</p>
          {deleteTarget.pattern_count > 0 && (
            <p className="text-yellow-600 dark:text-yellow-400">
              {deleteTarget.pattern_count} linked ad pattern
              {deleteTarget.pattern_count === 1 ? '' : 's'} will be unlinked (kept, not deleted).
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Note: if this name is detected again, or it is part of the seeded list, it can reappear.
          </p>
        </DeleteConfirmModal>
      )}
    </div>
  );
}

function NormalizationsSection() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<SponsorNormalization | null | undefined>(undefined);
  const [deleteId, setDeleteId] = useState<number | null>(null);

  const { data: norms, isLoading, error } = useQuery({
    queryKey: ['normalizations'],
    queryFn: getNormalizations,
  });

  const del = useMutation({
    mutationFn: (id: number) => deleteNormalization(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['normalizations'] });
      setDeleteId(null);
    },
  });

  if (isLoading) return <LoadingSpinner className="py-12" />;
  if (error) return <div className="text-center py-12"><p className="text-destructive">Failed to load normalizations</p></div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="text-sm text-muted-foreground">{norms?.length || 0} rules</div>
        <button
          type="button"
          onClick={() => setEditing(null)}
          className="px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
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
              <span className="shrink-0 px-2 py-0.5 text-xs rounded bg-slate-500/15 text-slate-700 dark:text-slate-300">{n.category}</span>
            </div>
            <div className="text-sm text-foreground mb-3 break-all">
              <span className="text-muted-foreground">→ </span>{n.canonical}
            </div>
            <div className="flex gap-2">
              <button onClick={() => setEditing(n)} className="px-2 py-1 text-xs rounded border border-border hover:bg-accent">Edit</button>
              <button onClick={() => setDeleteId(n.id)} className="px-2 py-1 text-xs rounded border border-destructive/40 text-destructive hover:bg-destructive/10">Delete</button>
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
                    <span className="px-2 py-0.5 text-xs rounded bg-slate-500/15 text-slate-700 dark:text-slate-300">{n.category}</span>
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">
                    <div className="flex gap-1">
                      <button onClick={() => setEditing(n)} className="px-2 py-1 rounded border border-border hover:bg-accent">Edit</button>
                      <button onClick={() => setDeleteId(n.id)} className="px-2 py-1 rounded border border-destructive/40 text-destructive hover:bg-destructive/10">Delete</button>
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
        <DeleteConfirmModal
          title="Delete normalization?"
          pending={del.isPending}
          onCancel={() => setDeleteId(null)}
          onConfirm={() => del.mutate(deleteId)}
        >
          <p className="text-muted-foreground">This permanently removes the rule.</p>
        </DeleteConfirmModal>
      )}
    </div>
  );
}

function Pagination({
  page, totalPages, total, onPage,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPage: (p: number) => void;
}) {
  if (totalPages <= 1) return null;
  const pages: (number | 'ellipsis')[] = [];
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1);
    if (page > 3) pages.push('ellipsis');
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) pages.push(i);
    if (page < totalPages - 2) pages.push('ellipsis');
    pages.push(totalPages);
  }
  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 mt-4 bg-card rounded-lg border border-border">
      <div className="text-sm text-muted-foreground">Page {page} of {totalPages} ({total} total)</div>
      <div className="flex items-center gap-1 sm:gap-2 flex-wrap justify-center">
        <button
          onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page === 1}
          className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
        >
          Previous
        </button>
        {pages.map((p, i) =>
          p === 'ellipsis' ? (
            <span key={`e${i}`} className="px-2 text-muted-foreground">...</span>
          ) : (
            <button
              key={p}
              onClick={() => onPage(p)}
              className={`px-3 py-1.5 text-sm rounded transition-colors ${
                p === page
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
              }`}
            >
              {p}
            </button>
          )
        )}
        <button
          onClick={() => onPage(Math.min(totalPages, page + 1))}
          disabled={page === totalPages}
          className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
        >
          Next
        </button>
      </div>
    </div>
  );
}

export default SponsorsPage;
