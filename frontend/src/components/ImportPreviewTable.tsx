import { ReactNode } from 'react';
import type { ImportPlanEntry, ImportRejectedFile } from '../api/feeds';
import { formatDate } from '../utils/format';
import { formatStorage } from '../pages/settings/settingsUtils';

interface Props {
  entries: ImportPlanEntry[];
  rejected: ImportRejectedFile[];
  totals: { importable: number; rejected: number; errors: number; bytes: number };
}

type RowStatus = 'ok' | 'warning' | 'error';

function rowStatus(entry: ImportPlanEntry): { status: RowStatus; reason: string | null } {
  if (entry.errors.length > 0) return { status: 'error', reason: entry.errors.join('; ') };
  if (entry.warnings.length > 0) return { status: 'warning', reason: entry.warnings.join('; ') };
  return { status: 'ok', reason: null };
}

const STATUS_CLASS: Record<RowStatus, string> = {
  ok: 'bg-success/20 text-success',
  warning: 'bg-warning/20 text-warning',
  error: 'bg-destructive/20 text-destructive',
};

function StatusBadge({ entry }: { entry: ImportPlanEntry }) {
  const { status, reason } = rowStatus(entry);
  return (
    <span
      className={`px-1.5 py-0.5 text-xs rounded font-medium cursor-help ${STATUS_CLASS[status]}`}
      title={reason ?? undefined}
    >
      {status}
    </span>
  );
}

const SIDECARS: { key: 'txt' | 'jpg' | 'json'; field: keyof ImportPlanEntry }[] = [
  { key: 'txt', field: 'descriptionFile' },
  { key: 'jpg', field: 'artworkFile' },
  { key: 'json', field: 'sidecarFile' },
];

function SidecarDots({ entry }: { entry: ImportPlanEntry }) {
  return (
    <span className="inline-flex items-center gap-2">
      {SIDECARS.map(({ key, field }) => {
        const filename = entry[field] as string | null;
        return (
          <span
            key={key}
            className={`inline-flex items-center gap-1 text-xs ${filename ? 'text-foreground' : 'text-muted-foreground/40'}`}
            title={filename ?? `no .${key} sidecar`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${filename ? 'bg-success' : 'bg-muted-foreground/30'}`} />
            {key}
          </span>
        );
      })}
    </span>
  );
}

function DateCell({ entry }: { entry: ImportPlanEntry }) {
  return (
    <span className="whitespace-nowrap">
      {formatDate(entry.publishedAt)}
      {entry.publishedAtSource === 'synthesized' && (
        <span
          className="ml-1.5 px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground align-middle"
          title="No explicit publish date found; one was assigned so episodes sort in order."
        >
          synthesized
        </span>
      )}
    </span>
  );
}

const HEADER_CLASS = 'py-2 pr-4 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider';

interface Column {
  label: string;
  render: (entry: ImportPlanEntry) => ReactNode;
}

function ReplacesPill({ entry }: { entry: ImportPlanEntry }) {
  // Only for a collision that will actually go through as an overwrite on
  // commit (replacesExisting + no errors) -- a collision that errored out
  // instead (overwrite off) already has its own error badge/reason and
  // doesn't need a second marker saying the same thing twice.
  if (!entry.replacesExisting || entry.errors.length > 0) return null;
  return (
    <span
      className="px-1.5 py-0.5 text-xs rounded font-medium bg-warning/20 text-warning"
      title="An episode with this ID already exists; committing replaces it."
    >
      replaces
    </span>
  );
}

const COLUMNS: Column[] = [
  {
    label: 'Episode',
    render: (e) => (
      <span className="inline-flex items-center gap-1.5">
        {e.episodeId}
        <ReplacesPill entry={e} />
      </span>
    ),
  },
  { label: 'Title', render: (e) => <span className="block max-w-xs truncate" title={e.title}>{e.title}</span> },
  { label: 'Date', render: (e) => <DateCell entry={e} /> },
  { label: 'Sidecars', render: (e) => <SidecarDots entry={e} /> },
  { label: 'Status', render: (e) => <StatusBadge entry={e} /> },
];

function ImportPreviewTable({ entries, rejected, totals }: Props) {
  return (
    <div>
      <table className="hidden sm:table w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            {COLUMNS.map((col, i) => (
              <th key={col.label} className={i === COLUMNS.length - 1 ? `${HEADER_CLASS} pr-0` : HEADER_CLASS}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.episodeId} className="border-b border-border/50 last:border-b-0">
              {COLUMNS.map((col, i) => (
                <td key={col.label} className={i === COLUMNS.length - 1 ? 'py-2 whitespace-nowrap' : 'py-2 pr-4'}>
                  {col.render(entry)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="sm:hidden space-y-3">
        {entries.map((entry) => (
          <div key={entry.episodeId} className="bg-card border border-border rounded-lg p-4 text-sm">
            <div className="flex items-center justify-between gap-2 mb-2 font-medium">
              <span className="inline-flex items-center gap-1.5">
                {entry.episodeId}
                <ReplacesPill entry={entry} />
              </span>
              <StatusBadge entry={entry} />
            </div>
            <p className="truncate mb-2" title={entry.title}>{entry.title}</p>
            <dl className="space-y-1">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground shrink-0">Date</dt>
                <dd className="text-right"><DateCell entry={entry} /></dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground shrink-0">Sidecars</dt>
                <dd className="text-right"><SidecarDots entry={entry} /></dd>
              </div>
            </dl>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>{totals.importable} importable</span>
        <span>{totals.errors} with errors</span>
        <span>{totals.rejected} rejected</span>
        <span>{formatStorage(totals.bytes / (1024 * 1024))} total</span>
      </div>

      {rejected.length > 0 && (
        <div className="mt-3">
          <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Rejected files</h4>
          <ul className="text-sm space-y-1">
            {rejected.map((r) => (
              <li key={r.file} className="flex flex-col sm:flex-row sm:justify-between sm:gap-3">
                <span className="truncate font-medium">{r.file}</span>
                <span className="text-xs text-muted-foreground">{r.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default ImportPreviewTable;
