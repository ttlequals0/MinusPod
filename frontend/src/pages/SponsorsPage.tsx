import { useState } from 'react';
import { useUrlParams, useUrlSearch, useUrlSort } from '../hooks/useUrlParams';
import QueryError from '../components/QueryError';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getSponsors, deleteSponsor } from '../api/sponsors';
import { getTagVocabulary } from '../api/community';
import { Sponsor } from '../api/types';
import SponsorEditModal from '../components/SponsorEditModal';
import { ConfirmModal } from '../components/Modal';
import { TagChips } from '../components/TagChips';
import { SegmentCategoryBadge } from '../components/SegmentCategoryBadge';
import { Skeleton, SkeletonRows } from '../components/Skeleton';
import { Pagination } from '../components/Pagination';
import { tableBody, tableCard, tableHead, tableRow, td, tdEmpty, tdMuted, th, thBase, rowCard } from '../components/tableStyles';
import { SortHeader } from '../components/SortHeader';
import SortControls from '../components/SortControls';
import { formatDate } from '../utils/format';
import Checkbox from '../components/Checkbox';
import { Plus } from 'lucide-react';
import { ActiveBadge } from '../components/ActiveBadge';
import { btnBase, btnPrimary, btnOutline, btnDestructiveOutline } from '../components/buttonStyles';
import { DemoDisabled } from '../components/DemoDisabled';
import { focusRing, inputBase, selectBase, filterLabel, filterControl } from '../components/fieldStyles';
import { tableActionBtn } from '../components/rowActionStyles';

type SortField = 'name' | 'category' | 'pattern_count' | 'created_at' | 'last_matched_at';
const SORT_FIELDS: SortField[] = ['name', 'category', 'pattern_count', 'created_at', 'last_matched_at'];

// Page, filters, search and sort live in the URL so Back and reload return to the same place.
const SPONSOR_PARAM_DEFAULTS = { page: '1', tag: 'all', inactive: '' };

export function SponsorsSection() {
  const queryClient = useQueryClient();
  const { read, readPage, write } = useUrlParams(SPONSOR_PARAM_DEFAULTS, { resetKey: 'page' });
  const tagFilter = read('tag');
  const showInactive = read('inactive') === '1';
  const [searchInput, setSearchInput, search] = useUrlSearch();
  const { sortBy: sortField, sortDirection, onSort: handleSort } = useUrlSort(SORT_FIELDS, 'name', 'asc');
  const [editing, setEditing] = useState<Sponsor | null | undefined>(undefined); // undefined = closed, null = new
  const [deleteTarget, setDeleteTarget] = useState<Sponsor | null>(null);
  const limit = 20;

  const { data: sponsors, isLoading, error, refetch, isFetching } = useQuery({
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
  const page = readPage('page', totalPages);
  const setPage = (next: number) => write({ page: String(next) });
  const paginated = sorted?.slice((page - 1) * limit, page * limit);

  if (isLoading) {
    return (
      <div>
        <div
          className="bg-card rounded-lg border border-border p-4 mb-6"
          role="status" aria-busy="true" aria-label="Loading"
          data-testid="skeleton-filter-card"
        >
          <div className="flex flex-wrap gap-4 items-center">
            <Skeleton className="h-8 w-40" />
            <Skeleton className="h-8 flex-1 min-w-[200px]" />
            <Skeleton className="h-8 w-28" />
            <Skeleton className="h-8 w-32" />
          </div>
        </div>
        <SkeletonRows count={6} />
      </div>
    );
  }
  if (error && !sponsors) return <QueryError title="Could not load sponsors." onRetry={() => void refetch()} retrying={isFetching} />;

  return (
    <div>
      {error && <div className="mb-6"><QueryError title="Sponsors could not be refreshed. Showing the last loaded list." onRetry={() => void refetch()} retrying={isFetching} /></div>}
      {/* Filters + add */}
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <div className="grid grid-cols-1 min-[375px]:grid-cols-2 lg:grid-cols-4 gap-4 items-end">
          <div className="min-w-0">
            <label htmlFor="sponsors-tag" className={filterLabel}>Tag</label>
            <select
              id="sponsors-tag"
              value={tagFilter}
              onChange={(e) => write({ tag: e.target.value })}
              className={`${filterControl} ${selectBase}`}
            >
              <option value="all">All</option>
              {(vocab?.all_tags ?? []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="min-w-0 order-first min-[375px]:col-span-2 lg:order-none lg:col-span-1">
            <label htmlFor="sponsors-search" className={filterLabel}>Search</label>
            <input
              id="sponsors-search"
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by name, alias, category..."
              className={`${filterControl} ${inputBase}`}
            />
          </div>
          <Checkbox
            className="min-h-11"
            checked={showInactive}
            onChange={(v) => write({ inactive: v ? '1' : '' })}
            label="Show inactive"
            labelClassName="text-sm text-muted-foreground"
          />
          <button
            type="button"
            onClick={() => setEditing(null)}
            className={`${btnBase} ${btnPrimary} min-[375px]:col-span-2 lg:col-span-1`}
          >
            <Plus className="w-4 h-4" aria-hidden="true" />
            Add sponsor
          </button>
        </div>
      </div>

      <div className="text-sm text-muted-foreground mb-3">{sorted?.length || 0} sponsors</div>

      <SortControls className="xl:hidden" field={sortField} direction={sortDirection} onSort={handleSort} options={[
        { value: 'name', label: 'Name' }, { value: 'category', label: 'Category' }, { value: 'pattern_count', label: 'Patterns' },
        { value: 'created_at', label: 'Created' }, { value: 'last_matched_at', label: 'Last matched' },
      ]} />
      {/* Card layout: the 8-column table needs about 1100 CSS px with touch-sized controls */}
      <div className="xl:hidden space-y-3 mb-4">
        {paginated?.map((s) => (
          <div key={s.id} className={rowCard}>
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="text-sm font-medium text-foreground">{s.name}</div>
              <ActiveBadge active={s.is_active} />
            </div>
            {s.aliases.length > 0 && (
              <div className="text-xs text-muted-foreground mb-1 truncate">{s.aliases.join(', ')}</div>
            )}
            {(s.category || s.segment_category) && (
              <div className="flex flex-wrap items-center gap-2 mb-2">
                {s.category && <span className="text-xs text-muted-foreground">{s.category}</span>}
                {s.segment_category && <SegmentCategoryBadge category={s.segment_category} />}
              </div>
            )}
            <TagChips tags={s.tags} className="mb-2" />
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground mb-3">
              <span>{s.pattern_count} patterns</span>
              <span>created {formatDate(s.created_at)}</span>
              <span>matched {formatDate(s.last_matched_at)}</span>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setEditing(s)} className={`${btnBase} ${btnOutline}`}>Edit</button>
              <DemoDisabled>
                <button onClick={() => setDeleteTarget(s)} className={`${btnBase} ${btnDestructiveOutline}`}>Delete</button>
              </DemoDisabled>
            </div>
          </div>
        ))}
        {paginated?.length === 0 && (
          <div className={`${rowCard} p-8 text-center text-muted-foreground`}>No sponsors yet.</div>
        )}
      </div>

      {/* Desktop table */}
      <div className={`hidden xl:block ${tableCard}`}>
        <div className="overflow-x-auto">
          <table className="w-full table-fixed">
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
            <thead className={tableHead}>
              <tr>
                <SortHeader field="name" label="Name" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={th}>Aliases</th>
                <SortHeader field="category" label="Category" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={th}>Tags</th>
                <SortHeader field="pattern_count" label="Patterns" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="created_at" label="Created" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="last_matched_at" label="Last matched" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={`${thBase} px-2 text-left`}>Actions</th>
              </tr>
            </thead>
            <tbody className={tableBody}>
              {paginated?.map((s) => (
                <tr key={s.id} className={tableRow}>
                  <td className={`${td} overflow-hidden`}>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground truncate">{s.name}</span>
                      {!s.is_active && <ActiveBadge active={false} />}
                    </div>
                  </td>
                  <td className={`${td} overflow-hidden`}>
                    <div className="text-xs text-muted-foreground truncate">{s.aliases.join(', ') || '-'}</div>
                  </td>
                  <td className={`${td} overflow-hidden`}>
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-sm text-muted-foreground truncate">{s.category || '-'}</span>
                      {s.segment_category && <SegmentCategoryBadge category={s.segment_category} />}
                    </div>
                  </td>
                  <td className={`${td} overflow-hidden`}><TagChips tags={s.tags} /></td>
                  <td className="px-2 py-3 whitespace-nowrap text-sm text-foreground">{s.pattern_count}</td>
                  <td className={`${tdMuted} whitespace-nowrap`}>{formatDate(s.created_at)}</td>
                  <td className={`${tdMuted} whitespace-nowrap`}>{formatDate(s.last_matched_at)}</td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">
                    <div className="flex gap-1">
                      <button onClick={() => setEditing(s)} className={`${tableActionBtn} ${btnOutline} ${focusRing}`}>Edit</button>
                      <DemoDisabled>
                        <button onClick={() => setDeleteTarget(s)} className={`${tableActionBtn} ${btnDestructiveOutline} ${focusRing}`}>Delete</button>
                      </DemoDisabled>
                    </div>
                  </td>
                </tr>
              ))}
              {paginated?.length === 0 && (
                <tr><td colSpan={8} className={tdEmpty}>No sponsors yet.</td></tr>
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
