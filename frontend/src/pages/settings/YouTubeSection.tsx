import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import CollapsibleSection from '../../components/CollapsibleSection';
import { SkeletonRows } from '../../components/Skeleton';
import ToggleSwitch from '../../components/ToggleSwitch';
import NumberInput from '../../components/NumberInput';
import { getErrorMessage } from '../../api/client';
import {
  getYouTubeSettings,
  updateYouTubeSettings,
  testYouTubeKey,
  saveYouTubeCookies,
  clearYouTubeCookies,
  updateYtDlpNow,
  resetYouTubeSettings,
  type YouTubeSettings,
  type YouTubeSettingsUpdate,
} from '../../api/youtube';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { inputBase, labelBase, textareaBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';
import { formatDateTime } from '../../utils/format';
import SavedBadge from './SavedBadge';
import ConfirmResetButton from './ConfirmResetButton';
import QueryError from '../../components/QueryError';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';

type NumberField = keyof YouTubeSettings['bounds'];

const NUMBER_ROWS: { field: NumberField; label: string }[] = [
  { field: 'processDelayMinutes', label: 'Processing delay (minutes, 0 = as soon as discovered)' },
  { field: 'sbProbeAttempts', label: 'SponsorBlock probe attempts' },
  { field: 'sbProbeIntervalMinutes', label: 'Probe interval (minutes)' },
  { field: 'sbWatchIntervalHours', label: 'Watch interval (hours)' },
  { field: 'sbWatchMaxDays', label: 'Watch ceiling (days)' },
];

function YouTubeSection() {
  const qc = useQueryClient();
  const { data, isPending, isError, isFetching, refetch } = useQuery({ queryKey: ['youtubeSettings'], queryFn: getYouTubeSettings });

  const [keyDraft, setKeyDraft] = useState('');
  const [cookiesDraft, setCookiesDraft] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyTest, setKeyTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [cookiesError, setCookiesError] = useState<string | null>(null);
  const [argsDraft, setArgsDraft] = useState<string | null>(null);
  const [proxyDraft, setProxyDraft] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<{ field: 'proxyUrl' | 'ytdlpExtraArgs' | null; message: string } | null>(null);
  const [updateNote, setUpdateNote] = useState<{ ok: boolean; text: string } | null>(null);
  useUnsavedChanges(!!keyDraft || !!cookiesDraft
    || (argsDraft !== null && argsDraft.trim() !== data?.ytdlpExtraArgs)
    || (proxyDraft !== null && proxyDraft.trim() !== data?.proxyUrl));

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['youtubeSettings'] });
    qc.invalidateQueries({ queryKey: ['settings'] });
  };

  const setCookies = (cookies: YouTubeSettings['cookies']) => {
    qc.setQueryData<YouTubeSettings>(['youtubeSettings'], (old) => (old ? { ...old, cookies } : old));
  };

  const saveSettings = useMutation({
    mutationFn: (p: YouTubeSettingsUpdate) => updateYouTubeSettings(p),
    onSuccess: (updated) => { qc.setQueryData(['youtubeSettings'], updated); setSettingsError(null); qc.invalidateQueries({ queryKey: ['settings'] }); },
    onError: (e: unknown, values) => {
      const field = 'proxyUrl' in values ? 'proxyUrl' : 'ytdlpExtraArgs' in values ? 'ytdlpExtraArgs' : null;
      setSettingsError({ field, message: getErrorMessage(e, 'Could not save YouTube settings.') });
    },
  });

  const saveKey = useMutation({
    mutationFn: (apiKey: string | null) => updateYouTubeSettings({ apiKey }),
    onSuccess: () => { setKeyError(null); setKeyTest(null); setKeyDraft(''); refresh(); },
    onError: (e: unknown) => setKeyError(getErrorMessage(e, 'Could not save the API key.')),
  });

  const testKey = useMutation({
    mutationFn: () => testYouTubeKey(keyDraft || undefined),
    onSuccess: (r) => setKeyTest({
      ok: r.ok,
      text: r.ok ? `Key works${r.channelTitle ? ` (${r.channelTitle})` : ''}.` : 'Key was rejected.',
    }),
    onError: (e: unknown) => setKeyTest({ ok: false, text: getErrorMessage(e, 'Could not test the API key.') }),
  });

  const saveCookies = useMutation({
    mutationFn: () => saveYouTubeCookies(cookiesDraft),
    onSuccess: (cookies) => { setCookiesError(null); setCookiesDraft(''); setCookies(cookies); },
    onError: (e: unknown) => setCookiesError(getErrorMessage(e, 'Could not save cookies.')),
  });

  const clearCookies = useMutation({
    mutationFn: clearYouTubeCookies,
    onSuccess: (cookies) => { setCookiesError(null); setCookiesDraft(''); setCookies(cookies); },
    onError: (e: unknown) => setCookiesError(getErrorMessage(e, 'Could not clear cookies.')),
  });

  const updateNow = useMutation({
    mutationFn: updateYtDlpNow,
    onSuccess: (r) => {
      if (r.error) setUpdateNote({ ok: false, text: r.error });
      else setUpdateNote({ ok: true, text: r.updated ? `Updated to ${r.version ?? 'the latest version'}.` : 'Already up to date.' });
      refresh();
    },
    onError: (e: unknown) => setUpdateNote({ ok: false, text: getErrorMessage(e, 'Could not update yt-dlp.') }),
  });

  const resetAll = useMutation({
    mutationFn: resetYouTubeSettings,
    onSuccess: () => { setSettingsError(null); setArgsDraft(null); setProxyDraft(null); refresh(); },
    onError: (e: unknown) => setSettingsError({ field: null, message: getErrorMessage(e, 'Could not reset YouTube settings.') }),
  });
  const proxyErr = settingsError?.field === 'proxyUrl' ? settingsError.message : null;
  const argsErr = settingsError?.field === 'ytdlpExtraArgs' ? settingsError.message : null;
  const settingsBusy = saveSettings.isPending || resetAll.isPending;
  const keyBusy = saveKey.isPending || testKey.isPending;
  const cookiesBusy = saveCookies.isPending || clearCookies.isPending;

  return (
    <CollapsibleSection title="YouTube">
      {isError ? (
        <QueryError title="Could not load YouTube settings." onRetry={() => void refetch()} retrying={isFetching} />
      ) : isPending || !data ? (
        <SkeletonRows count={4} />
      ) : (
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.enabled}
                onChange={(enabled) => saveSettings.mutate({ enabled })}
                disabled={settingsBusy}
                ariaLabel="Enable YouTube feeds"
              />
              <span className="text-sm font-medium text-foreground">Enable YouTube feeds</span>
            </label>
            <p className="text-sm text-muted-foreground">
              Turns YouTube videos, playlists and channels into podcast feeds with yt-dlp.
            </p>
            {settingsError && !settingsError.field && <p role="alert" className="text-sm text-destructive">{settingsError.message}</p>}
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <label htmlFor="yt-api-key" className={labelBase}>YouTube Data API key (optional)</label>
            <input
              id="yt-api-key"
              type="password"
              autoComplete="off"
              className={`${inputBase} w-full max-w-md`}
              placeholder={data.apiKeyConfigured ? 'Stored' : ''}
              value={keyDraft}
              disabled={keyBusy}
              onChange={(e) => setKeyDraft(e.target.value)}
              aria-invalid={keyError ? true : undefined}
              aria-describedby={keyError ? 'yt-api-key-error' : undefined}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!keyDraft.trim() || keyBusy}
                onClick={() => saveKey.mutate(keyDraft)}
              >
                {saveKey.isPending ? 'Saving...' : 'Save key'}
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!data.apiKeyConfigured || keyBusy}
                onClick={() => saveKey.mutate(null)}
              >
                Clear key
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={keyBusy || (!keyDraft.trim() && !data.apiKeyConfigured)}
                onClick={() => { setKeyTest(null); testKey.mutate(); }}
              >
                {testKey.isPending ? 'Testing...' : 'Test key'}
              </button>
              {saveKey.isSuccess && !keyError && <SavedBadge />}
            </div>
            {keyError && <p id="yt-api-key-error" role="alert" className="text-sm text-destructive">{keyError}</p>}
            {keyTest && (
              <p role={keyTest.ok ? 'status' : 'alert'} className={`text-sm ${keyTest.ok ? 'text-success' : 'text-destructive'}`}>
                {keyTest.text}
              </p>
            )}
          </div>

          <div className="pt-3 border-t border-border space-y-3">
            <h4 className={subHeading}>Processing</h4>
            {NUMBER_ROWS.map(({ field, label }) => (
              <div key={field} className="flex items-center justify-between gap-3">
                <label htmlFor={`yt-${field}`} className="text-sm text-foreground flex-1 min-w-0">{label}</label>
                <NumberInput
                  id={`yt-${field}`}
                  commitOn="blur"
                  className="w-24 shrink-0"
                  value={data[field]}
                  min={data.bounds[field][0]}
                  max={data.bounds[field][1]}
                  fallback={data[field]}
                  step={1}
                  parse={parseInt}
                  disabled={settingsBusy}
                  onCommit={(v) => { if (v !== data[field]) saveSettings.mutate({ [field]: v }); }}
                />
              </div>
            ))}
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.llmFallback}
                onChange={(llmFallback) => saveSettings.mutate({ llmFallback })}
                disabled={settingsBusy}
                ariaLabel="Fall back to the LLM pipeline when no marks appear"
              />
              <span className="text-sm text-foreground">Fall back to the LLM pipeline when no marks appear</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.captionsAsTranscript}
                onChange={(captionsAsTranscript) => saveSettings.mutate({ captionsAsTranscript })}
                disabled={settingsBusy}
                ariaLabel="Use YouTube captions as the transcript"
              />
              <span className="text-sm text-foreground">Use YouTube captions as the transcript</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.hidePrivate}
                onChange={(hidePrivate) => saveSettings.mutate({ hidePrivate })}
                disabled={settingsBusy}
                ariaLabel="Hide private and deleted episodes"
              />
              <span className="text-sm text-foreground">Hide private and deleted episodes</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.transcribeFallback}
                onChange={(transcribeFallback) => saveSettings.mutate({ transcribeFallback })}
                disabled={settingsBusy}
                ariaLabel="Transcribe when captions are missing"
              />
              <span className="text-sm text-foreground">Transcribe when captions are missing</span>
            </label>
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <label htmlFor="yt-cookies" className={labelBase}>Cookies</label>
            <textarea
              id="yt-cookies"
              rows={4}
              className={`${textareaBase} w-full font-mono`}
              value={cookiesDraft}
              disabled={cookiesBusy}
              onChange={(e) => setCookiesDraft(e.target.value)}
              spellCheck={false}
              aria-describedby={`yt-cookies-help${cookiesError ? ' yt-cookies-error' : ''}`}
              aria-invalid={cookiesError ? true : undefined}
            />
            <p id="yt-cookies-help" className="text-sm text-muted-foreground">
              Paste a Netscape cookies.txt exported from a throwaway account. Used only by yt-dlp.
            </p>
            <p className="text-sm text-muted-foreground">
              {data.cookies.set
                ? `${data.cookies.lines.toLocaleString()} lines, updated ${formatDateTime(data.cookies.updatedAt)}`
                : 'No cookies stored.'}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!cookiesDraft.trim() || cookiesBusy}
                onClick={() => saveCookies.mutate()}
              >
                {saveCookies.isPending ? 'Saving...' : 'Save cookies'}
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!data.cookies.set || cookiesBusy}
                onClick={() => clearCookies.mutate()}
              >
                {clearCookies.isPending ? 'Clearing...' : 'Clear cookies'}
              </button>
            </div>
            {cookiesError && <p id="yt-cookies-error" role="alert" className="text-sm text-destructive">{cookiesError}</p>}
            {(saveCookies.isSuccess || clearCookies.isSuccess) && !cookiesError && <SavedBadge />}
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <h4 className={subHeading}>yt-dlp</h4>
            <div className="space-y-1">
              <label htmlFor="yt-proxy-url" className={labelBase}>Proxy URL</label>
              <input
                id="yt-proxy-url"
                type="text"
                autoComplete="off"
                spellCheck={false}
                className={`${inputBase} w-full font-mono`}
                value={proxyDraft ?? data.proxyUrl}
                disabled={settingsBusy}
                onChange={(e) => setProxyDraft(e.target.value)}
                onBlur={() => {
                  if (proxyDraft === null) return;
                  if (proxyDraft.trim() === data.proxyUrl) setProxyDraft(null);
                  else saveSettings.mutate({ proxyUrl: proxyDraft }, { onSuccess: () => setProxyDraft(null) });
                }}
                aria-invalid={!!proxyErr}
                aria-describedby={`yt-proxy-url-help${proxyErr ? ' yt-proxy-url-error' : ''}`}
              />
              <p id="yt-proxy-url-help" className="text-sm text-muted-foreground">
                Used for yt-dlp and YouTube Data API requests. Example: socks5h://proxy:1080
              </p>
              {proxyErr && <p id="yt-proxy-url-error" role="alert" className="text-sm text-destructive">{proxyErr}</p>}
            </div>
            <div className="space-y-1">
              <label htmlFor="yt-extra-args" className={labelBase}>Extra yt-dlp arguments</label>
              <input
                id="yt-extra-args"
                type="text"
                autoComplete="off"
                spellCheck={false}
                className={`${inputBase} w-full font-mono`}
                value={argsDraft ?? data.ytdlpExtraArgs}
                disabled={settingsBusy}
                onChange={(e) => setArgsDraft(e.target.value)}
                onBlur={() => {
                  if (argsDraft === null) return;
                  if (argsDraft.trim() === data.ytdlpExtraArgs) setArgsDraft(null);
                  else saveSettings.mutate({ ytdlpExtraArgs: argsDraft }, { onSuccess: () => setArgsDraft(null) });
                }}
                aria-invalid={!!argsErr}
                aria-describedby={`yt-extra-args-help${argsErr ? ' yt-extra-args-error' : ''}`}
              />
              <p id="yt-extra-args-help" className="text-sm text-muted-foreground">
                Added before MinusPod&apos;s options on every yt-dlp call. Example: --limit-rate 2M --extractor-args youtube:player_client=web
              </p>
              {argsErr && <p id="yt-extra-args-error" role="alert" className="text-sm text-destructive">{argsErr}</p>}
            </div>
            <div className="mt-3 text-sm text-muted-foreground">
              <div>
                yt-dlp {data.ytdlp.version ?? 'not installed'}
                {data.ytdlp.bakedVersion ? ` (image ships ${data.ytdlp.bakedVersion})` : ''}
              </div>
              <div>Last checked {formatDateTime(data.ytdlp.lastCheckAt)}</div>
              {data.ytdlp.lastError && <div className="text-destructive">{data.ytdlp.lastError}</div>}
            </div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.ytdlp.autoUpdate}
                onChange={(ytdlpAutoUpdate) => saveSettings.mutate({ ytdlpAutoUpdate })}
                disabled={settingsBusy}
                ariaLabel="Update yt-dlp daily"
              />
              <span className="text-sm text-foreground">Update yt-dlp daily</span>
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={updateNow.isPending}
                onClick={() => { setUpdateNote(null); updateNow.mutate(); }}
              >
                {updateNow.isPending ? 'Updating...' : 'Update now'}
              </button>
              {updateNote && (
                <span role={updateNote.ok ? 'status' : 'alert'} className={`text-sm ${updateNote.ok ? 'text-success' : 'text-destructive'}`}>
                  {updateNote.text}
                </span>
              )}
            </div>
          </div>

          <div className="pt-3 border-t border-border flex flex-wrap items-center gap-2">
            <ConfirmResetButton
              label="Reset to defaults"
              confirmHint="Reset YouTube settings to their defaults, including the daily yt-dlp update toggle? The feature switch, Data API key and cookies file are kept."
              isPending={resetAll.isPending}
              disabled={saveSettings.isPending}
              onConfirm={() => resetAll.mutate()}
            />
            {resetAll.isSuccess && !settingsError && <SavedBadge />}
          </div>
        </div>
      )}
    </CollapsibleSection>
  );
}

export default YouTubeSection;
