import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSettings, updateSettings, resetSettings, resetPrompts, getModels, getWhisperModels, getSystemStatus, runCleanup, getProcessingEpisodes, cancelProcessing, refreshModels } from '../api/settings';
import { setPassword, removePassword } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import LoadingSpinner from '../components/LoadingSpinner';
import CollapsibleSection from '../components/CollapsibleSection';
import type { ClaudeModel } from '../api/types';

function formatModelLabel(model: ClaudeModel): string {
  if (model.inputCostPerMtok != null && model.outputCostPerMtok != null) {
    const fmtIn = model.inputCostPerMtok % 1 === 0
      ? model.inputCostPerMtok.toFixed(0) : model.inputCostPerMtok.toFixed(2);
    const fmtOut = model.outputCostPerMtok % 1 === 0
      ? model.outputCostPerMtok.toFixed(0) : model.outputCostPerMtok.toFixed(2);
    return `${model.name} ($${fmtIn} / $${fmtOut} per MTok)`;
  }
  return model.name;
}

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
  const [verificationPrompt, setVerificationPrompt] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [verificationModel, setVerificationModel] = useState('');
  const [whisperModel, setWhisperModel] = useState('');
  const [autoProcessEnabled, setAutoProcessEnabled] = useState(true);
  const [audioBitrate, setAudioBitrate] = useState('128k');
  const [vttTranscriptsEnabled, setVttTranscriptsEnabled] = useState(true);
  const [chaptersEnabled, setChaptersEnabled] = useState(true);
  const [chaptersModel, setChaptersModel] = useState('');
  const [minCutConfidence, setMinCutConfidence] = useState(0.80);
  const [llmProvider, setLlmProvider] = useState('anthropic');
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState('http://localhost:8000/v1');
  const [hasChanges, setHasChanges] = useState(false);
  const [cleanupConfirm, setCleanupConfirm] = useState(false);

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  });

  const { data: models, isLoading: modelsLoading } = useQuery({
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
    refetchInterval: 5000,
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
      setVerificationPrompt(settings.verificationPrompt?.value || '');
      setSelectedModel(settings.claudeModel?.value || '');
      setVerificationModel(settings.verificationModel?.value || '');
      setWhisperModel(settings.whisperModel?.value || 'small');
      setAutoProcessEnabled(settings.autoProcessEnabled?.value ?? true);
      setAudioBitrate(settings.audioBitrate?.value || '128k');
      setVttTranscriptsEnabled(settings.vttTranscriptsEnabled?.value ?? true);
      setChaptersEnabled(settings.chaptersEnabled?.value ?? true);
      setChaptersModel(settings.chaptersModel?.value || '');
      setMinCutConfidence(settings.minCutConfidence?.value ?? 0.80);
      setLlmProvider(settings.llmProvider?.value || 'anthropic');
      setOpenaiBaseUrl(settings.openaiBaseUrl?.value || 'http://localhost:8000/v1');
    }
  }, [settings]);

  useEffect(() => {
    if (settings) {
      const changed =
        systemPrompt !== (settings.systemPrompt?.value || '') ||
        verificationPrompt !== (settings.verificationPrompt?.value || '') ||
        selectedModel !== (settings.claudeModel?.value || '') ||
        verificationModel !== (settings.verificationModel?.value || '') ||
        whisperModel !== (settings.whisperModel?.value || 'small') ||
        autoProcessEnabled !== (settings.autoProcessEnabled?.value ?? true) ||
        audioBitrate !== (settings.audioBitrate?.value || '128k') ||
        vttTranscriptsEnabled !== (settings.vttTranscriptsEnabled?.value ?? true) ||
        chaptersEnabled !== (settings.chaptersEnabled?.value ?? true) ||
        chaptersModel !== (settings.chaptersModel?.value || '') ||
        minCutConfidence !== (settings.minCutConfidence?.value ?? 0.80) ||
        llmProvider !== (settings.llmProvider?.value || 'anthropic') ||
        openaiBaseUrl !== (settings.openaiBaseUrl?.value || 'http://localhost:8000/v1');
      setHasChanges(changed);
    }
  }, [systemPrompt, verificationPrompt, selectedModel, verificationModel, whisperModel, autoProcessEnabled, audioBitrate, vttTranscriptsEnabled, chaptersEnabled, chaptersModel, minCutConfidence, llmProvider, openaiBaseUrl, settings]);

  const updateMutation = useMutation({
    mutationFn: () =>
      updateSettings({
        systemPrompt,
        verificationPrompt,
        claudeModel: selectedModel,
        verificationModel,
        whisperModel,
        autoProcessEnabled,
        audioBitrate,
        vttTranscriptsEnabled,
        chaptersEnabled,
        chaptersModel,
        minCutConfidence,
        llmProvider,
        openaiBaseUrl,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
      setHasChanges(false);
    },
  });

  const refreshModelsMutation = useMutation({
    mutationFn: refreshModels,
    onSuccess: (data) => {
      queryClient.setQueryData(['models'], data.models);
    },
  });

  const resetMutation = useMutation({
    mutationFn: resetSettings,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['models'] });
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

  const formatTokenCount = (tokens: number): string => {
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
    return String(tokens);
  };

  const formatCost = (cost: number): string => {
    return `$${cost.toFixed(2)}`;
  };

  if (settingsLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4 pb-20">
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
          API Docs
        </a>
      </div>

      {/* 1. System Status */}
      <CollapsibleSection title="System Status" defaultOpen>
        {statusLoading ? (
          <LoadingSpinner size="sm" />
        ) : status ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Version</p>
              <a
                href="https://github.com/ttlequals0/minuspod"
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
            <div>
              <p className="text-sm text-muted-foreground">LLM Tokens</p>
              <p className="font-medium text-foreground">
                {formatTokenCount(status.stats?.totalInputTokens ?? 0)} in / {formatTokenCount(status.stats?.totalOutputTokens ?? 0)} out
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">LLM Cost</p>
              <p className="font-medium text-foreground">{formatCost(status.stats?.totalLlmCost ?? 0)}</p>
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
      </CollapsibleSection>

      {/* 2. Security */}
      <CollapsibleSection
        title="Security"
        subtitle={isPasswordSet ? 'Password protection is enabled' : 'No password set'}
      >
        <div className="flex justify-end mb-4">
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
      </CollapsibleSection>

      {/* 3. Processing Queue */}
      <CollapsibleSection title="Processing Queue">
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
      </CollapsibleSection>

      {/* 4. LLM Provider */}
      <CollapsibleSection title="LLM Provider" defaultOpen>
        <div className="space-y-4">
          <div>
            <label htmlFor="llmProvider" className="block text-sm font-medium text-foreground mb-2">
              Provider
            </label>
            <select
              id="llmProvider"
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="anthropic">Anthropic</option>
              <option value="openai-compatible">OpenAI Compatible</option>
              <option value="ollama">Ollama</option>
            </select>
          </div>

          {llmProvider !== 'anthropic' && (
            <div>
              <label htmlFor="openaiBaseUrl" className="block text-sm font-medium text-foreground mb-2">
                Base URL
              </label>
              <input
                type="text"
                id="openaiBaseUrl"
                value={openaiBaseUrl}
                onChange={(e) => setOpenaiBaseUrl(e.target.value)}
                placeholder="http://localhost:11434/v1"
                className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm"
              />
              <p className="mt-1 text-sm text-muted-foreground">
                {llmProvider === 'ollama'
                  ? 'Ollama server URL (e.g. http://localhost:11434)'
                  : 'OpenAI-compatible API endpoint (must end with /v1)'}
              </p>
            </div>
          )}

          <div>
            <p className="text-sm font-medium text-foreground mb-1">API Key Status</p>
            {settings?.apiKeyConfigured ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                Configured (env)
              </span>
            ) : llmProvider === 'anthropic' ? (
              <>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-500/10 text-yellow-600 dark:text-yellow-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                  Not configured
                </span>
                <p className="mt-2 text-sm text-muted-foreground">
                  Set ANTHROPIC_API_KEY environment variable to enable Anthropic API access
                </p>
              </>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-muted text-muted-foreground">
                <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
                Not required
              </span>
            )}
          </div>
        </div>
      </CollapsibleSection>

      {/* 5. AI Models */}
      <CollapsibleSection
        title="AI Models"
        headerRight={
          <button
            onClick={() => refreshModelsMutation.mutate()}
            disabled={refreshModelsMutation.isPending}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            title="Refresh model list from provider"
          >
            {refreshModelsMutation.isPending ? (
              <>
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Refreshing...
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </>
            )}
          </button>
        }
      >
        {!modelsLoading && models && models.length === 0 && (
          <div className="mb-4 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
            <p className="text-sm text-yellow-600 dark:text-yellow-400">
              No models available from the LLM provider. Check that your provider is configured correctly and the endpoint is reachable.
            </p>
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label htmlFor="model" className="block text-sm font-medium text-foreground mb-2">
              Ad Detection Model
            </label>
            <select
              id="model"
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {models?.map((model) => (
                <option key={model.id} value={model.id}>
                  {formatModelLabel(model)}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Primary model for analyzing transcripts and detecting ads
            </p>
          </div>

          <div>
            <label htmlFor="verificationModel" className="block text-sm font-medium text-foreground mb-2">
              Verification Model
            </label>
            <select
              id="verificationModel"
              value={verificationModel}
              onChange={(e) => setVerificationModel(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {models?.map((model) => (
                <option key={model.id} value={model.id}>
                  {formatModelLabel(model)}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Re-runs detection on processed audio to catch missed ads (can differ for cost optimization)
            </p>
          </div>

          <div>
            <label htmlFor="chaptersModel" className="block text-sm font-medium text-foreground mb-2">
              Chapters Model
            </label>
            <select
              id="chaptersModel"
              value={chaptersModel}
              onChange={(e) => setChaptersModel(e.target.value)}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {models?.map((model) => (
                <option key={model.id} value={model.id}>
                  {formatModelLabel(model)}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted-foreground">
              Chapter title generation and topic detection (smaller/cheaper models work well)
            </p>
          </div>
        </div>
      </CollapsibleSection>

      {/* 6. Transcription */}
      <CollapsibleSection title="Transcription">
        <div>
          <label htmlFor="whisperModel" className="block text-sm font-medium text-foreground mb-2">
            Whisper Model
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
      </CollapsibleSection>

      {/* 7. Audio */}
      <CollapsibleSection title="Audio">
        <div className="space-y-4">
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

          <div className="pt-3 border-t border-border">
            <h3 className="text-sm font-medium text-foreground mb-1">Audio Analysis</h3>
            <p className="text-sm text-muted-foreground">
              Volume and transition analysis runs automatically on every episode. Detects volume anomalies and abrupt loudness transitions that indicate dynamically inserted ads. Audio signals are included as context in the AI detection prompt.
            </p>
          </div>
        </div>
      </CollapsibleSection>

      {/* 8. Ad Detection */}
      <CollapsibleSection title="Ad Detection">
        <div className="space-y-6">
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
            </p>
          </div>

          <div className="pt-3 border-t border-border">
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
              <span className="text-sm font-medium text-foreground">Auto-Process New Episodes</span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-14">
              Automatically download and process new episodes when feeds are refreshed. Individual podcasts can override this setting.
            </p>
          </div>
        </div>
      </CollapsibleSection>

      {/* 9. Podcasting 2.0 */}
      <CollapsibleSection title="Podcasting 2.0">
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
      </CollapsibleSection>

      {/* 10. Prompts */}
      <CollapsibleSection title="Prompts">
        <div className="space-y-6">
          <div>
            <label htmlFor="systemPrompt" className="block text-sm font-medium text-foreground mb-2">
              First Pass System Prompt
            </label>
            <textarea
              id="systemPrompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm sm:rows-12"
            />
            <p className="mt-1 text-sm text-muted-foreground">
              Instructions sent to the AI model for the initial ad detection pass
            </p>
          </div>

          <div>
            <label htmlFor="verificationPrompt" className="block text-sm font-medium text-foreground mb-2">
              Verification Prompt
            </label>
            <textarea
              id="verificationPrompt"
              value={verificationPrompt}
              onChange={(e) => setVerificationPrompt(e.target.value)}
              rows={6}
              className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono text-sm sm:rows-12"
            />
            <p className="mt-1 text-sm text-muted-foreground">
              Instructions for the verification pass to detect ads missed by the first pass
            </p>
          </div>

          <button
            onClick={() => resetPromptsMutation.mutate()}
            disabled={resetPromptsMutation.isPending}
            className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            {resetPromptsMutation.isPending ? 'Resetting...' : 'Reset Prompts to Default'}
          </button>
        </div>
      </CollapsibleSection>

      {/* Error display */}
      {(updateMutation.error || resetMutation.error || resetPromptsMutation.error) && (
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
          {((updateMutation.error || resetMutation.error || resetPromptsMutation.error) as Error).message}
        </div>
      )}

      {/* Sticky save bar */}
      {hasChanges && (
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background/80 backdrop-blur-md">
          <div className="max-w-3xl mx-auto flex items-center justify-between gap-4 px-4 py-3">
            <button
              onClick={() => resetMutation.mutate()}
              disabled={resetMutation.isPending}
              className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors text-sm"
            >
              {resetMutation.isPending ? 'Resetting...' : 'Reset All'}
            </button>
            <button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
              className="px-6 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors text-sm font-medium"
            >
              {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default Settings;
