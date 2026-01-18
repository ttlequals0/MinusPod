import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing } from '../api/settings';
import { setPassword, removePassword } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';

function Settings() {
  const queryClient = useQueryClient();
  const { isPasswordSet, logout, refreshStatus } = useAuth();

  // Password management state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [systemPrompt, setSystemPrompt] = useState('');
  const [secondPassPrompt, setSecondPassPrompt] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [secondPassModel, setSecondPassModel] = useState('');
  const [multiPassEnabled, setMultiPassEnabled] = useState(false);
  const [whisperModel, setWhisperModel] = useState('');
  const [audioAnalysisEnabled, setAudioAnalysisEnabled] = useState(false);
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(true);
  const [audioBitrate, setAudioBitrate] = useState('128k');
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(true);
  const [chaptersEnabled, setChaptersEnabled] = useState(true);
  const [minCutConfidence, setMinCutConfidence] = useState(0.80);
  const [hasChanges, setHasChanges] = useState(false);
  const [cleanupConfirm, setCleanupConfirm] = useState(false);

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const { data: models } = useQuery({
    queryKey: ['models'],
    queryFn: getModels,
  });

  const { data: whisperModels } = useQuery({
    queryKey: ['whisperModels'],
    queryFn: getWhisperModels,
  });

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['status'],
    queryFn: getSystemStatus,
  });

  const { data: processingEpisodes } = useQuery({
    queryKey: ['processing-episodes'],
    queryFn: getProcessingEpisodes,
    refetchInterval: 5000, // Poll every 5 seconds
  });

  const cancelMutation = useMutation({
    mutationFn: (params: { slug: string; episodeId: string }) =>
      cancelProcessing(params.slug, params.episodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['processing-episodes'] });
      queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });

  useEffect(() => {
    if (settings) {
      setSystemPrompt(settings.systemPrompt?.value || '');
      setSecondPassPrompt(settings.secondPassPrompt?.value || '');
      setSelectedModel(settings.claudeModel?.value || '');
      setSecondPassModel(settings.secondPassModel?.value || '');
      setMultiPassEnabled(settings.multiPassEnabled?.value ?? false);
      setWhisperModel(settings.whisperModel?.value || 'small');
      setAudioAnalysisEnabled(settings.audioAnalysisEnabled?.value ?? false);
      setAutoProcessEnabled(settings.autoProcessEnabled?.value ?? true);
      setAudioBitrate(settings.audioBitrate?.value || '128k');
      setVttTranscriptsEnabled(settings.vttTranscriptsEnabled?.value ?? true);
      setChaptersEnabled(settings.chaptersEnabled?.value ?? true);
      setMinCutConfidence(settings.minCutConfidence?.value ?? 0.80);
    }
  }, [settings]);

  useEffect(() => {
    if (settings) {
      const changed =
        systemPrompt !== (settings.systemPrompt?.value || '') ||
        secondPassPrompt !== (settings.secondPassPrompt?.value || '') ||
        selectedModel !== (settings.claudeModel?.value || '') ||
        secondPassModel !== (settings.secondPassModel?.value || '') ||
        multiPassEnabled !== (settings.multiPassEnabled?.value ?? false) ||
        whisperModel !== (settings.whisperModel?.value || 'small') ||
        audioAnalysisEnabled !== (settings.audioAnalysisEnabled?.value ?? false) ||
        autoProcessEnabled !== (settings.autoProcessEnabled?.value ?? true) ||
        audioBitrate !== (settings.audioBitrate?.value || '128k') ||
        vttTranscriptsEnabled !== (settings.vttTranscriptsEnabled?.value ?? true) ||
        chaptersEnabled !== (settings.chaptersEnabled?.value ?? true) ||
        minCutConfidence !== (settings.minCutConfidence?.value ?? 0.80);
      setHasChanges(changed);
    }
  }, [systemPrompt, secondPassPrompt, selectedModel, secondPassModel, multiPassEnabled, whisperModel, audioAnalysisEnabled, autoProcessEnabled, audioBitrate, vttTranscriptsEnabled, chaptersEnabled, minCutConfidence, settings]);

  const updateMutation = useMutation({
    mutationFn: () =>
      updateSettings({
        systemPrompt,
        secondPassPrompt,
        claudeModel: selectedModel,
        secondPassModel,
        multiPassEnabled,
        whisperModel,
        audioAnalysisEnabled,
        autoProcessEnabled,
        audioBitrate,
        vttTranscriptsEnabled,
        chaptersEnabled,
        minCutConfidence,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      setHasChanges(false);
    },
  });

  const resetMutation = useMutation({
    mutationFn: resetSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const resetPromptsMutation = useMutation({
    mutationFn: resetPrompts,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const cleanupMutation = useMutation({
    mutationFn: runCleanup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['status'] });
      setCleanupConfirm(false);
    },
  });

  const handleCleanup = () => {
    if (cleanupConfirm) {
      cleanupMutation.mutate();
    } else {
      setCleanupConfirm(true);
      setTimeout(() => setCleanupConfirm(false), 3000);
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);

    if (newPassword !== confirmPassword) {
      setPasswordError('Passwords do not match');
      return;
    }

    if (newPassword && newPassword.length < 8) {
      setPasswordError('Password must be at least 8 characters');
      return;
    }

    setIsChangingPassword(true);
    try {
      if (newPassword) {
        await setPassword(newPassword, currentPassword);
        setPasswordSuccess(isPasswordSet ? 'Password changed successfully' : 'Password set successfully');
      } else {
        await removePassword(currentPassword);
        setPasswordSuccess('Password protection removed');
      }
      await refreshStatus();
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (error) {
      setPasswordError((error as Error).message);
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    window.location.href = '/ui/login';
  };

  const formatUptime = (seconds: number) => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '0:00';
    const totalSecs = Math.floor(seconds);
    const hours = Math.floor(totalSecs / 3600);
    const minutes = Math.floor((totalSecs % 3600) / 60);
    const secs = totalSecs % 60;
    if (hours > 0) {
      return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  };

  if (settingsLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <div className="flex justify-between items-start">
        <div>
          <h1 className="text-2xl font-bold text-foreground mb-2">Settings</h1>
          <p className="text-muted-foreground">
            Configure ad detection prompts and system settings
          </p>
        </div>
        <a
          href="/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-primary hover:underline flex items-center gap-1"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          API Documentation
        </a>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">System Status</h2>
        {statusLoading ? (
          <LoadingSpinner size="sm" />
        ) : status ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Version</p>
              <a
                href="https://github.com/ttlequals0/podcast-server"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary hover:underline"
              >
                {status.version}
              </a>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Feeds</p>
              <p className="font-medium text-foreground">{status.feeds?.total ?? 0}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Episodes</p>
              <p className="font-medium text-foreground">{status.episodes?.total ?? 0}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Storage</p>
              <p className="font-medium text-foreground">{status.storage?.usedMb?.toFixed(1) ?? 0} MB</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Uptime</p>
              <p className="font-medium text-foreground">{formatUptime(status.uptime ?? 0)}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Time Saved</p>
              <p className="font-medium text-foreground">{formatDuration(status.stats?.totalTimeSaved ?? 0)}</p>
            </div>
          </div>
        ) : null}
        <div className="mt-4 pt-4 border-t border-border">
          <button
            onClick={handleCleanup}
            disabled={cleanupMutation.isPending}
            className={`px-4 py-2 rounded transition-colors disabled:opacity-50 ${
              cleanupConfirm
                ? 'bg-destructive text-destructive-foreground hover:bg-destructive/80'
                : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
            }`}
          >
            {cleanupMutation.isPending
              ? 'Deleting...'
              : cleanupConfirm
              ? 'Click again to confirm'
              : 'Delete All Episodes'}
          </button>
          {cleanupMutation.data && (
            <span className="ml-3 text-sm text-muted-foreground">
              Deleted {cleanupMutation.data.episodesRemoved} episodes
            </span>
          )}
        </div>
      </div>

      {/* Security Section */}
      <div className="bg-card rounded-lg border border-border p-6">
        <div className="flex justify-between items-start mb-4">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Security</h2>
            <p className="text-sm text-muted-foreground mt-1">
              {isPasswordSet ? 'Password protection is enabled' : 'No password set - app is publicly accessible'}
            </p>
          </div>
          {isPasswordSet && (
            <button
              onClick={handleLogout}
              className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors"
            >
              Logout
            </button>
          )}
        </div>

        {!isPasswordSet && (
          <div className="mb-4 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
            <p className="text-sm text-yellow-600 dark:text-yellow-400">
              This application has no password protection. Anyone with network access can view and modify data.
            </p>
          </div>
        )}

        <form onSubmit={handlePasswordSubmit} className="space-y-4">
          {isPasswordSet && (
            <div>
              <label htmlFor="currentPassword" className="block text-sm font-medium text-foreground mb-2">
                Current Password
              </label>
              <input
                type="password"
                id="currentPassword"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          )}

          <div>
            <label htmlFor="newPassword" className="block text-sm font-medium text-foreground mb-2">
              {isPasswordSet ? 'New Password' : 'Set Password'}
            </label>
            <input
              type="password"
              id="newPassword"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder={isPasswordSet ? 'Leave empty to remove password' : 'Minimum 8 characters'}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          <div>
            <label htmlFor="confirmPassword" className="block text-sm font-medium text-foreground mb-2">
              Confirm Password
            </label>
            <input
              type="password"
              id="confirmPassword"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {passwordError && (
            <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
              {passwordError}
            </div>
          )}

          {passwordSuccess && (
            <div className="p-3 rounded-lg bg-green-500/10 text-green-600 dark:text-green-400 text-sm">
              {passwordSuccess}
            </div>
          )}

          <button
            type="submit"
            disabled={isChangingPassword || (!isPasswordSet && !newPassword)}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {isChangingPassword
              ? 'Saving...'
              : isPasswordSet
              ? newPassword
                ? 'Change Password'
                : 'Remove Password'
              : 'Set Password'}
          </button>
        </form>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Processing Queue</h2>
        {processingEpisodes && processingEpisodes.length > 0 ? (
          <div className="space-y-2">
            {processingEpisodes.map((episode) => (
              <div
                key={`${episode.slug}-${episode.episodeId}`}
                className="bg-secondary/50 rounded-lg p-4 flex justify-between items-center"
              >
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-foreground truncate">{episode.title}</p>
                  <p className="text-sm text-muted-foreground">{episode.podcast}</p>
                </div>
                <button
                  onClick={() => cancelMutation.mutate({ slug: episode.slug, episodeId: episode.episodeId })}
                  disabled={cancelMutation.isPending}
                  className="px-3 py-1 text-sm rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50 transition-colors ml-4 flex-shrink-0"
                >
                  {cancelMutation.isPending ? 'Canceling...' : 'Cancel'}
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No episodes currently processing</p>
        )}
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Whisper Model</h2>
        <div>
          <label htmlFor="whisperModel" className="block text-sm font-medium text-foreground mb-2">
            Model for Transcription
          </label>
          <select
            id="whisperModel"
            value={whisperModel}
            onChange={(e) => setWhisperModel(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {whisperModels?.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name} - {model.vram} VRAM, {model.quality}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Larger models produce better transcriptions but require more GPU memory
          </p>
          {whisperModels && (
            <div className="mt-3 text-xs text-muted-foreground">
              <span className="font-medium">Current:</span> {whisperModels.find(m => m.id === whisperModel)?.speed || ''}
            </div>
          )}
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Audio Output Quality</h2>
        <div>
          <label htmlFor="audioBitrate" className="block text-sm font-medium text-foreground mb-2">
            Output Bitrate
          </label>
          <select
            id="audioBitrate"
            value={audioBitrate}
            onChange={(e) => setAudioBitrate(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="64k">64 kbps - Smallest file size</option>
            <option value="96k">96 kbps - Good for speech</option>
            <option value="128k">128 kbps - Standard quality (recommended)</option>
            <option value="192k">192 kbps - High quality</option>
            <option value="256k">256 kbps - Maximum quality</option>
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Higher bitrates produce better audio quality but larger file sizes
          </p>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Audio Analysis</h2>
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <div
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                audioAnalysisEnabled ? 'bg-primary' : 'bg-secondary'
              }`}
              onClick={() => setAudioAnalysisEnabled(!audioAnalysisEnabled)}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  audioAnalysisEnabled ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </div>
            <span className="text-sm font-medium text-foreground">Enable Audio Analysis</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Analyze audio characteristics (volume changes, music detection, speaker patterns) to improve ad detection accuracy. Experimental feature.
          </p>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Ad Detection Aggressiveness</h2>
        <div>
          <label htmlFor="minCutConfidence" className="block text-sm font-medium text-foreground mb-2">
            Minimum Confidence Threshold: {Math.round(minCutConfidence * 100)}%
          </label>
          <input
            type="range"
            id="minCutConfidence"
            min="0.50"
            max="0.95"
            step="0.05"
            value={minCutConfidence}
            onChange={(e) => setMinCutConfidence(parseFloat(e.target.value))}
            className="w-full h-2 bg-muted rounded-lg appearance-none cursor-pointer accent-primary"
          />
          <div className="flex justify-between text-xs text-muted-foreground mt-1">
            <span>More Aggressive (50%)</span>
            <span>More Conservative (95%)</span>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            Controls how confident the system must be before removing an ad.
            Lower values remove more potential ads but may include false positives.
            Higher values are safer but may miss some ads.
          </p>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Auto-Process New Episodes</h2>
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <div
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                autoProcessEnabled ? 'bg-primary' : 'bg-secondary'
              }`}
              onClick={() => setAutoProcessEnabled(!autoProcessEnabled)}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  autoProcessEnabled ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </div>
            <span className="text-sm font-medium text-foreground">Enable Auto-Processing</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Automatically download and process new episodes when feeds are refreshed. Individual podcasts can override this setting.
          </p>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Claude Model</h2>
        <div>
          <label htmlFor="model" className="block text-sm font-medium text-foreground mb-2">
            Model for Ad Detection
          </label>
          <select
            id="model"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {models?.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
          <p className="mt-1 text-sm text-muted-foreground">
            Select which Claude model to use for analyzing transcripts
          </p>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Multi-Pass Detection</h2>
        <div>
          <label className="flex items-center gap-3 cursor-pointer">
            <div
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                multiPassEnabled ? 'bg-primary' : 'bg-secondary'
              }`}
              onClick={() => setMultiPassEnabled(!multiPassEnabled)}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  multiPassEnabled ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </div>
            <span className="text-sm font-medium text-foreground">Enable Multi-Pass Ad Detection</span>
          </label>
          <p className="mt-2 text-sm text-muted-foreground ml-14">
            Run a second detection pass on processed audio to catch missed ads. Increases processing time and API costs.
          </p>
        </div>

        {multiPassEnabled && (
          <div className="mt-6 pt-4 border-t border-border">
            <label htmlFor="secondPassModel" className="block text-sm font-medium text-foreground mb-2">
              Second Pass Model
            </label>
            <select
              id="secondPassModel"
              value={secondPassModel}
              onChange={(e) => setSecondPassModel(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {models?.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Model for second pass detection (can differ from first pass for cost optimization)
            </p>
          </div>
        )}
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Podcasting 2.0</h2>
        <div className="space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  vttTranscriptsEnabled ? 'bg-primary' : 'bg-secondary'
                }`}
                onClick={() => setVttTranscriptsEnabled(!vttTranscriptsEnabled)}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    vttTranscriptsEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </div>
              <span className="text-sm font-medium text-foreground">Generate VTT Transcripts</span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              Create WebVTT transcripts with adjusted timestamps for podcast apps
            </p>
          </div>

          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <div
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  chaptersEnabled ? 'bg-primary' : 'bg-secondary'
                }`}
                onClick={() => setChaptersEnabled(!chaptersEnabled)}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    chaptersEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </div>
              <span className="text-sm font-medium text-foreground">Generate Chapters</span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              Create JSON chapters from ad boundaries and description timestamps
            </p>
          </div>
        </div>
      </div>

      <div className="bg-card rounded-lg border border-border p-6">
        <h2 className="text-lg font-semibold text-foreground mb-4">Ad Detection Prompt</h2>

        <div>
          <label htmlFor="systemPrompt" className="block text-sm font-medium text-foreground mb-2">
            First Pass System Prompt
          </label>
          <textarea
            id="systemPrompt"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={12}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Instructions sent to Claude for the initial ad detection pass
          </p>
        </div>

        {multiPassEnabled && (
          <div className="mt-6">
            <label htmlFor="secondPassPrompt" className="block text-sm font-medium text-foreground mb-2">
              Second Pass System Prompt
            </label>
            <textarea
              id="secondPassPrompt"
              value={secondPassPrompt}
              onChange={(e) => setSecondPassPrompt(e.target.value)}
              rows={12}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
            />
            <p className="mt-1 text-sm text-muted-foreground">
              Instructions for the second pass to detect subtle or baked-in ads missed by the first pass
            </p>
          </div>
        )}

        {(updateMutation.error || resetMutation.error || resetPromptsMutation.error) && (
          <div className="mt-4 p-4 rounded-lg bg-destructive/10 text-destructive">
            {((updateMutation.error || resetMutation.error || resetPromptsMutation.error) as Error).message}
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-4">
          <button
            onClick={() => updateMutation.mutate()}
            disabled={updateMutation.isPending || !hasChanges}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
          </button>
          <button
            onClick={() => resetPromptsMutation.mutate()}
            disabled={resetPromptsMutation.isPending}
            className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            {resetPromptsMutation.isPending ? 'Resetting...' : 'Reset Prompts Only'}
          </button>
          <button
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
            className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            {resetMutation.isPending ? 'Resetting...' : 'Reset All Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default Settings;
