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
  type YouTubeSettings,
  type YouTubeSettingsUpdate,
} from '../../api/youtube';
import { btnBase, btnSecondary } from '../../components/buttonStyles';
import { inputBase, labelBase, textareaBase } from '../../components/fieldStyles';
import { subHeading } from '../../components/pageStyles';
import { formatDateTime } from '../../utils/format';
import SavedBadge from './SavedBadge';

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
  const { data, isLoading } = useQuery({ queryKey: ['youtubeSettings'], queryFn: getYouTubeSettings });

  const [keyDraft, setKeyDraft] = useState('');
  const [cookiesDraft, setCookiesDraft] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyTest, setKeyTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [cookiesError, setCookiesError] = useState<string | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [updateNote, setUpdateNote] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['youtubeSettings'] });
    qc.invalidateQueries({ queryKey: ['settings'] });
  };

  const setCookies = (cookies: YouTubeSettings['cookies']) => {
    qc.setQueryData<YouTubeSettings>(['youtubeSettings'], (old) => (old ? { ...old, cookies } : old));
  };

  const saveSettings = useMutation({
    mutationFn: (p: YouTubeSettingsUpdate) => updateYouTubeSettings(p),
    onSuccess: () => { setSettingsError(null); refresh(); },
    onError: (e: unknown) => setSettingsError(getErrorMessage(e, 'Could not save YouTube settings.')),
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

  return (
    <CollapsibleSection title="YouTube">
      {isLoading || !data ? (
        <SkeletonRows count={4} />
      ) : (
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.enabled}
                onChange={(enabled) => saveSettings.mutate({ enabled })}
                disabled={saveSettings.isPending}
                ariaLabel="Enable YouTube feeds"
              />
              <span className="text-sm font-medium text-foreground">Enable YouTube feeds</span>
            </label>
            <p className="text-sm text-muted-foreground">
              Turns playlists and channels into podcast feeds with yt-dlp. Check YouTube&apos;s terms of service for your use before enabling.
            </p>
            {settingsError && <p role="alert" className="text-sm text-destructive">{settingsError}</p>}
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
              onChange={(e) => setKeyDraft(e.target.value)}
              aria-invalid={keyError ? true : undefined}
              aria-describedby={keyError ? 'yt-api-key-error' : undefined}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!keyDraft || saveKey.isPending}
                onClick={() => saveKey.mutate(keyDraft)}
              >
                {saveKey.isPending ? 'Saving...' : 'Save key'}
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!data.apiKeyConfigured || saveKey.isPending}
                onClick={() => saveKey.mutate(null)}
              >
                Clear key
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={testKey.isPending || (!keyDraft && !data.apiKeyConfigured)}
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
                  disabled={saveSettings.isPending}
                  onCommit={(v) => { if (v !== data[field]) saveSettings.mutate({ [field]: v }); }}
                />
              </div>
            ))}
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.llmFallback}
                onChange={(llmFallback) => saveSettings.mutate({ llmFallback })}
                disabled={saveSettings.isPending}
                ariaLabel="Fall back to the LLM pipeline when no marks appear"
              />
              <span className="text-sm text-foreground">Fall back to the LLM pipeline when no marks appear</span>
            </label>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={data.captionsAsTranscript}
                onChange={(captionsAsTranscript) => saveSettings.mutate({ captionsAsTranscript })}
                disabled={saveSettings.isPending}
                ariaLabel="Use YouTube captions as the transcript"
              />
              <span className="text-sm text-foreground">Use YouTube captions as the transcript</span>
            </label>
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <label htmlFor="yt-cookies" className={labelBase}>Cookies</label>
            <textarea
              id="yt-cookies"
              rows={4}
              className={`${textareaBase} w-full font-mono`}
              value={cookiesDraft}
              onChange={(e) => setCookiesDraft(e.target.value)}
              spellCheck={false}
              aria-describedby="yt-cookies-help"
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
                disabled={!cookiesDraft.trim() || saveCookies.isPending}
                onClick={() => saveCookies.mutate()}
              >
                {saveCookies.isPending ? 'Saving...' : 'Save cookies'}
              </button>
              <button
                type="button"
                className={`${btnBase} ${btnSecondary}`}
                disabled={!data.cookies.set || clearCookies.isPending}
                onClick={() => clearCookies.mutate()}
              >
                Clear cookies
              </button>
            </div>
            {cookiesError && <p role="alert" className="text-sm text-destructive">{cookiesError}</p>}
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <h4 className={subHeading}>yt-dlp</h4>
            <div className="text-sm text-muted-foreground">
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
                disabled={saveSettings.isPending}
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
        </div>
      )}
    </CollapsibleSection>
  );
}

export default YouTubeSection;
