import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  applyConfigImport,
  ConfigImportPreview,
  downloadRuntimeConfig,
  previewConfigImport,
  RuntimeConfigDocument,
} from '../../api/configTransfer';
import { getErrorMessage } from '../../api/client';
import { btnSecondary } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';

type Scope = 'everything' | 'global' | 'feeds';

function ConfigurationTransferSection() {
  const queryClient = useQueryClient();
  const [downloadAcknowledged, setDownloadAcknowledged] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [document, setDocument] = useState<RuntimeConfigDocument | null>(null);
  const [scope, setScope] = useState<Scope>('everything');
  const [selectedFeeds, setSelectedFeeds] = useState<string[]>([]);
  const [preview, setPreview] = useState<ConfigImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [reading, setReading] = useState(false);
  const fileGeneration = useRef(0);

  const handleFile = async (nextFile: File | null) => {
    const generation = ++fileGeneration.current;
    setFile(nextFile);
    setDocument(null);
    setPreview(null);
    setMessage('');
    setWarnings([]);
    setError('');
    setReading(false);
    if (!nextFile) return;
    try {
      if (nextFile.size > 10 * 1024 * 1024) {
        throw new Error('Configuration files must be no larger than 10 MiB.');
      }
      setReading(true);
      const parsed: unknown = JSON.parse(await nextFile.text());
      if (generation !== fileGeneration.current) return;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('The selected file must contain a JSON object.');
      }
      const typed = parsed as RuntimeConfigDocument;
      if (typed.format !== 'minuspod-runtime-config' || !Array.isArray(typed.feeds)
        || !typed.feeds.every((feed) => feed && typeof feed === 'object'
          && typeof feed.slug === 'string' && feed.slug.length > 0
          && ['subscribed', 'local', 'recents'].includes(feed.feedType)
          && feed.settings && typeof feed.settings === 'object' && !Array.isArray(feed.settings))) {
        throw new Error('This is not a MinusPod runtime configuration file.');
      }
      setDocument(typed);
      setSelectedFeeds(typed.feeds.map((feed) => feed.slug));
    } catch (err) {
      if (generation === fileGeneration.current) {
        setError(getErrorMessage(err, 'Could not read this configuration file.'));
      }
    } finally {
      if (generation === fileGeneration.current) setReading(false);
    }
  };

  const request = () => ({
    document: document!,
    scope,
    ...(scope === 'feeds' ? { selectedFeeds } : {}),
  });

  const handlePreview = async () => {
    if (!document) return;
    setBusy(true);
    setError('');
    setMessage('');
    setWarnings([]);
    try {
      setPreview(await previewConfigImport(request()));
    } catch (err) {
      setPreview(null);
      setError(getErrorMessage(err, 'Preview failed.'));
    } finally {
      setBusy(false);
    }
  };

  const handleApply = async () => {
    if (!document || !preview) return;
    setBusy(true);
    setError('');
    setMessage('');
    setWarnings([]);
    try {
      const result = await applyConfigImport({ ...request(), previewToken: preview.previewToken });
      setMessage('Configuration imported.');
      setWarnings(result.warnings);
      setPreview(null);
      await queryClient.invalidateQueries();
    } catch (err) {
      setError(getErrorMessage(err, 'Import failed. The file and choices are still selected.'));
    } finally {
      setBusy(false);
    }
  };

  const toggleFeed = (slug: string) => {
    setSelectedFeeds((current) => current.includes(slug)
      ? current.filter((value) => value !== slug)
      : [...current, slug]);
    setPreview(null);
  };

  return (
    <section className="mt-4 rounded-lg border border-border bg-background p-4" aria-labelledby="config-transfer-title">
      <h4 id="config-transfer-title" className="text-sm font-semibold text-foreground">
        Configuration Import / Export
      </h4>
      <p className="mt-1 text-xs text-muted-foreground">
        Export or merge global settings and feed configuration as JSON. This does not transfer episodes, history, patterns, database contents, or media files.
      </p>

      <div className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3">
        <label className="flex items-start gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={downloadAcknowledged}
            onChange={(event) => setDownloadAcknowledged(event.target.checked)}
            className={`mt-0.5 h-4 w-4 ${focusRing}`}
          />
          <span>The export contains configured credentials and private feed URLs. It does not contain the administrator sign-in password.</span>
        </label>
        <button
          type="button"
          disabled={!downloadAcknowledged || downloading}
          onClick={async () => {
            setDownloading(true);
            setError('');
            try {
              await downloadRuntimeConfig();
            } catch (err) {
              setError(getErrorMessage(err, 'Configuration export failed.'));
            } finally {
              setDownloading(false);
            }
          }}
          className={`mt-3 min-h-[44px] rounded-lg px-4 py-2 text-sm font-medium ${btnSecondary} ${focusRing} disabled:opacity-50`}
        >
          {downloading ? 'Preparing export...' : 'Download Configuration JSON'}
        </button>
      </div>

      <div className="mt-4 border-t border-border pt-4">
        <label htmlFor="config-import-file" className="block text-sm font-medium text-foreground">
          Import configuration JSON
        </label>
        <input
          id="config-import-file"
          type="file"
          disabled={busy}
          accept="application/json,.json"
          onChange={(event) => { void handleFile(event.target.files?.[0] ?? null); }}
          className={`mt-2 block min-h-[44px] w-full text-sm text-foreground file:mr-3 file:min-h-[44px] file:rounded-lg file:border-0 file:px-3 file:py-2 file:text-sm ${focusRing}`}
        />
        {file && <p className="mt-1 break-all text-xs text-muted-foreground">Selected: {file.name}</p>}
        <label htmlFor="config-import-scope" className="mt-3 block text-sm font-medium text-foreground">Import scope</label>
        <select
          id="config-import-scope"
          value={scope}
          disabled={busy}
          onChange={(event) => { setScope(event.target.value as Scope); setPreview(null); }}
          className={`mt-1 min-h-[44px] w-full ${selectBase}`}
        >
          <option value="everything">Global and feeds</option>
          <option value="global">Global settings only</option>
          <option value="feeds">Feeds only</option>
        </select>
        {scope === 'feeds' && document && (
          <fieldset disabled={busy} className="mt-3 max-h-48 overflow-y-auto rounded-md border border-border p-3">
            <legend className="px-1 text-sm font-medium text-foreground">Select feeds</legend>
            <div className="flex gap-3 pb-2 text-xs">
              <button type="button" className={`min-h-[44px] underline ${focusRing}`} onClick={() => { setSelectedFeeds(document.feeds.map((feed) => feed.slug)); setPreview(null); }}>Select all</button>
              <button type="button" className={`min-h-[44px] underline ${focusRing}`} onClick={() => { setSelectedFeeds([]); setPreview(null); }}>Clear selection</button>
            </div>
            {document.feeds.map((feed) => (
              <label key={feed.slug} className="flex min-h-[44px] items-center gap-2 text-sm text-foreground">
                <input type="checkbox" checked={selectedFeeds.includes(feed.slug)} onChange={() => toggleFeed(feed.slug)} />
                <span className="min-w-0 break-all">{feed.slug}</span>
              </label>
            ))}
          </fieldset>
        )}
        <button
          type="button"
          disabled={!document || busy || reading || (scope === 'feeds' && selectedFeeds.length === 0)}
          onClick={() => { void handlePreview(); }}
          className={`mt-3 min-h-[44px] rounded-lg px-4 py-2 text-sm font-medium ${btnSecondary} ${focusRing} disabled:opacity-50`}
        >
          {busy ? 'Working...' : 'Preview Import'}
        </button>
        {preview && (
          <div className="mt-3 rounded-md border border-border bg-secondary/30 p-3 text-sm text-foreground">
            <p>Preview: {preview.changedSettings.length} changed settings, {preview.addedFeeds.length} new feeds, {preview.updatedFeeds.length} updated feeds.</p>
            <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto break-all">
              {preview.changedSettings.map(({ key, secret }) => <li key={`setting-${key}`}>Setting: {key}{secret ? ' (credential)' : ''}</li>)}
              {preview.addedFeeds.map((slug) => <li key={`new-${slug}`}>New feed: {slug}</li>)}
              {preview.updatedFeeds.map((slug) => <li key={`updated-${slug}`}>Update feed: {slug}</li>)}
            </ul>
            <p className="mt-1 text-xs text-muted-foreground">Selected values merge into this instance. Feeds and settings absent from the selection are preserved. Credential values are never shown in this preview.</p>
            {preview.skippedUnknownSettings.length > 0 && <p className="mt-1 text-xs">Unknown settings skipped: {preview.skippedUnknownSettings.join(', ')}</p>}
            {preview.warnings.map((item) => <p key={item} className="mt-1 text-xs text-warning">{item}</p>)}
            <button
              type="button"
              disabled={busy}
              onClick={() => { void handleApply(); }}
              className={`mt-3 min-h-[44px] rounded-lg px-4 py-2 text-sm font-medium ${btnSecondary} ${focusRing} disabled:opacity-50`}
            >
              {busy ? 'Applying...' : 'Apply Import'}
            </button>
          </div>
        )}
        {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
        {message && <p role="status" className="mt-3 text-sm text-success">{message}</p>}
        {warnings.map((warning) => <p key={warning} role="status" className="mt-2 text-sm text-warning">{warning}</p>)}
      </div>
    </section>
  );
}

export default ConfigurationTransferSection;
