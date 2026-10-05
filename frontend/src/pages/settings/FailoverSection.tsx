import type {
  FailoverEvent, FailoverOverview, FailoverProbe, FailoverProbeName, FailoverTargetName, FailoverTargetState,
} from '../../api/failover';
import { FAILOVER_TARGET_LABELS, FAILOVER_TARGETS, failoverSourceLabel, failoverTargetLabel } from '../../api/failover';
import type { LlmProvider, WhisperBackend } from '../../api/types';
import { SLOT_LABELS, WHISPER_BACKENDS } from '../../api/types';
import type { ProviderStatus } from '../../api/providers';
import { testFailoverProviderConnection, testFailoverWhisperConnection } from '../../api/providers';
import type { ModelCatalog } from '../../hooks/useModelCatalog';
import CollapsibleSection from '../../components/CollapsibleSection';
import NumberInput from '../../components/NumberInput';
import DraftNumberInput, { parseOptionalNumber } from '../../components/DraftNumberInput';
import ToggleSwitch from '../../components/ToggleSwitch';
import { SkeletonRows } from '../../components/Skeleton';
import { badgeBase, tint } from '../../components/badgeStyles';
import { btnOutline, btnSecondary, touchTarget } from '../../components/buttonStyles';
import { focusRing, inputBase, selectBase } from '../../components/fieldStyles';
import { formatDateTime, formatTimeAgo } from '../../utils/format';
import ConnectionTestButton from './ConnectionTestButton';
import ModelSelect from './ModelSelect';
import ProviderKeyField from './ProviderKeyField';
import { ProviderFields, keyMetaForType } from './ProviderFields';

interface FailoverLlm {
  enabled: boolean;
  provider: LlmProvider | '';
  baseUrl: string;
  timeoutSeconds: number | null;
  maxRetries: number | null;
  detectionModel: string;
  reviewModel: string;
  verificationModel: string;
  chaptersModel: string;
  apiKeyConfigured: boolean;
}

interface FailoverWhisper {
  enabled: boolean;
  backend: WhisperBackend;
  model: string;
  apiBaseUrl: string;
  apiModel: string;
  apiTimeoutSeconds: number;
  maxAttempts: number | null;
  language: string;
  apiKeyConfigured: boolean;
}

interface FailoverSectionProps {
  storageKey?: string;
  onToggle?: (isOpen: boolean) => void;
  overview: FailoverOverview | undefined;
  overviewLoading: boolean;
  onTrigger: (t: FailoverTargetName) => void;
  onCancel: (t: FailoverTargetName) => void;
  onProbeNow: () => void;
  actionPending: boolean;
  probePending: boolean;
  actionError: string | null;
  probeIntervalMinutes: number;
  onProbeIntervalChange: (v: number) => void;
  recoveryProbes: number;
  onRecoveryProbesChange: (v: number) => void;
  llm: FailoverLlm;
  onLlmChange: (patch: Partial<FailoverLlm>) => void;
  onLlmApiKeySave: (key: string) => Promise<void>;
  onLlmApiKeyClear: () => Promise<void>;
  failoverCatalog: ModelCatalog;
  whisper: FailoverWhisper;
  activeWhisperMaxAttempts: number;
  onWhisperChange: (patch: Partial<FailoverWhisper>) => void;
  onWhisperApiKeySave: (key: string) => Promise<void>;
  onWhisperApiKeyClear: () => Promise<void>;
  skipFlacCompression: boolean;
  cryptoReady: boolean;
}

const EVENT_LIMIT = 20;

// Both LLM targets share one failover account, so either's flag says whether it is set up.
const STANDBY_ROWS: { label: string; target: FailoverTargetName; probe: FailoverProbeName }[] = [
  { label: 'LLM failover', target: 'llm-a', probe: 'llm-failover' },
  { label: 'Transcriber failover', target: 'transcriber', probe: 'transcriber-failover' },
];

function keyStatus(configured: boolean): ProviderStatus {
  return { configured, source: configured ? 'db' : 'none' };
}

function targetBadge(state: FailoverTargetState, probe: FailoverProbe | undefined) {
  if (state.active) return { label: 'Failed over', tone: tint.warning };
  return probeBadge(probe);
}

function probeBadge(probe: FailoverProbe | undefined) {
  if (!probe || probe.reachable === null) return { label: 'Unprobed', tone: tint.neutral };
  return probe.reachable
    ? { label: 'Healthy', tone: tint.success }
    : { label: 'Unreachable', tone: tint.destructive };
}

// "via <source>: <reason>", dropping a reason that only repeats the source.
function viaText(source: string | null, reason: string | null): string {
  const sourceText = source ? failoverSourceLabel(source) : '';
  const via = sourceText ? ` via ${sourceText}` : '';
  return reason && reason !== sourceText ? `${via}: ${reason}` : via;
}

function eventText(e: FailoverEvent): string {
  const target = failoverTargetLabel(e.target);
  if (e.action === 'trigger') return `${target} failed over${viaText(e.source, e.reason)}`;
  return `${target} returned to its own config, ${e.source === 'manual' ? 'cancelled manually' : 'recovered automatically'}`;
}

function targetMeta(state: FailoverTargetState, probe: FailoverProbe | undefined): string {
  if (state.active) {
    const since = state.since ? `Since ${formatTimeAgo(state.since)}` : 'Active';
    return `${since}${viaText(state.source, state.reason)}`;
  }
  return probeMeta(probe);
}

function probeMeta(probe: FailoverProbe | undefined): string {
  if (!probe?.checkedAt) return 'Not probed yet';
  const detail = probe.reachable === false && probe.detail ? `: ${probe.detail}` : '';
  return `Last probe ${formatTimeAgo(probe.checkedAt)}${detail}`;
}

function TargetRow({
  name, state, probe, pending, onTrigger, onCancel,
}: {
  name: FailoverTargetName;
  state: FailoverTargetState;
  probe: FailoverProbe | undefined;
  pending: boolean;
  onTrigger: (t: FailoverTargetName) => void;
  onCancel: (t: FailoverTargetName) => void;
}) {
  const label = FAILOVER_TARGET_LABELS[name];
  const badge = targetBadge(state, probe);
  const verb = state.active ? 'Cancel' : 'Trigger';
  return (
    <li className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-3 ${state.active ? 'bg-warning/5' : ''}`}>
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{label}</span>
          <span className={`${badgeBase} shrink-0 font-medium ${badge.tone}`}>{badge.label}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground break-words">{targetMeta(state, probe)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {!state.configured && (
          <span className="text-xs text-muted-foreground">Failover not configured</span>
        )}
        <button
          type="button"
          aria-label={`${verb} failover for ${label}`}
          disabled={pending || (!state.active && !state.configured)}
          onClick={() => (state.active ? onCancel(name) : onTrigger(name))}
          className={`shrink-0 px-3 py-1.5 rounded-md ${btnOutline} ${touchTarget} text-sm font-medium disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {verb} failover
        </button>
      </div>
    </li>
  );
}

// A standby account's own health: no action, so it reads as subordinate to the targets.
function StandbyRow({ label, configured, probe }: {
  label: string;
  configured: boolean;
  probe: FailoverProbe | undefined;
}) {
  const badge = configured ? probeBadge(probe) : { label: 'Not configured', tone: tint.neutral };
  return (
    <li className="px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
        <span className={`${badgeBase} shrink-0 font-medium ${badge.tone}`}>{badge.label}</span>
      </div>
      {configured && <p className="mt-1 text-xs text-muted-foreground break-words">{probeMeta(probe)}</p>}
    </li>
  );
}

function FailoverSection({
  storageKey, onToggle,
  overview, overviewLoading, onTrigger, onCancel, onProbeNow, actionPending, probePending, actionError,
  probeIntervalMinutes, onProbeIntervalChange, recoveryProbes, onRecoveryProbesChange,
  llm, onLlmChange, onLlmApiKeySave, onLlmApiKeyClear, failoverCatalog,
  whisper, activeWhisperMaxAttempts, onWhisperChange, onWhisperApiKeySave, onWhisperApiKeyClear, skipFlacCompression, cryptoReady,
}: FailoverSectionProps) {
  const keyMeta = keyMetaForType(llm.provider);
  const whisperKey = keyStatus(whisper.apiKeyConfigured);
  const events = overview?.events.slice(0, EVENT_LIMIT) ?? [];

  return (
    <CollapsibleSection
      title="Failover"
      storageKey={storageKey}
      onToggle={onToggle}
      subtitle="Switch to a standby provider when one is down. Both LLM providers and the transcriber can fail over."
      headerRight={(
        <button
          type="button"
          onClick={onProbeNow}
          disabled={actionPending}
          className={`px-2.5 py-1 text-xs rounded ${btnSecondary} ${touchTarget} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {probePending ? 'Probing...' : 'Probe now'}
        </button>
      )}
    >
      <div className="space-y-4">
        {overview ? (
          <ul aria-label="Failover status" className="rounded-lg border border-border divide-y divide-border overflow-hidden">
            {FAILOVER_TARGETS.map((name) => (
              <TargetRow
                key={name}
                name={name}
                state={overview.targets[name]}
                probe={overview.probes[name]}
                pending={actionPending}
                onTrigger={onTrigger}
                onCancel={onCancel}
              />
            ))}
            {STANDBY_ROWS.map((row) => (
              <StandbyRow
                key={row.probe}
                label={row.label}
                configured={overview.targets[row.target].configured}
                probe={overview.probes[row.probe]}
              />
            ))}
          </ul>
        ) : overviewLoading ? (
          <SkeletonRows count={5} />
        ) : (
          <p className="text-sm text-muted-foreground">Failover status could not be loaded.</p>
        )}

        {actionError && (
          <p role="alert" className="bg-destructive/10 text-destructive rounded-lg p-3 text-sm">{actionError}</p>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label htmlFor="failoverProbeIntervalMinutes" className="block text-sm font-medium text-foreground mb-2">
              Probe interval (minutes)
            </label>
            <NumberInput
              id="failoverProbeIntervalMinutes"
              value={probeIntervalMinutes}
              min={1}
              max={60}
              fallback={probeIntervalMinutes}
              step={1}
              parse={(s) => parseInt(s, 10)}
              onCommit={onProbeIntervalChange}
              className={`w-full ${inputBase}`}
            />
            <p className="mt-1 text-sm text-muted-foreground">How often each provider and transcriber is checked.</p>
          </div>
          <div>
            <label htmlFor="failoverRecoveryProbes" className="block text-sm font-medium text-foreground mb-2">
              Healthy probes before recovery
            </label>
            <NumberInput
              id="failoverRecoveryProbes"
              value={recoveryProbes}
              min={1}
              max={10}
              fallback={recoveryProbes}
              step={1}
              parse={(s) => parseInt(s, 10)}
              onCommit={onRecoveryProbesChange}
              className={`w-full ${inputBase}`}
            />
            <p className="mt-1 text-sm text-muted-foreground">
              An automatic failover ends after this many healthy checks in a row. A manual one stays until cancelled.
            </p>
          </div>
        </div>

        <div className="pt-4 border-t border-border space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={llm.enabled}
                onChange={(enabled) => onLlmChange({ enabled })}
                ariaLabel="Enable LLM failover"
              />
              <span className="text-sm font-medium text-foreground">LLM failover</span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-12">
              A standby LLM account that takes over {SLOT_LABELS.primary} or {SLOT_LABELS.secondary} traffic when that provider fails.
            </p>
          </div>

          {llm.enabled && (
            <>
              <ProviderFields
                providerSelectId="failoverLlmProvider"
                providerLabel="Failover provider type"
                provider={llm.provider}
                onProviderChange={(provider) => onLlmChange({
                  provider, baseUrl: '',
                  detectionModel: '', reviewModel: '', verificationModel: '', chaptersModel: '',
                })}
                baseUrlInputId="failoverLlmBaseUrl"
                baseUrlLabel="Failover base URL"
                baseUrl={llm.baseUrl}
                onBaseUrlChange={(baseUrl) => onLlmChange({ baseUrl })}
                slotLabel={SLOT_LABELS.failover}
                timeoutSeconds={llm.timeoutSeconds}
                onTimeoutChange={(timeoutSeconds) => onLlmChange({ timeoutSeconds })}
                maxRetries={llm.maxRetries}
                onMaxRetriesChange={(maxRetries) => onLlmChange({ maxRetries })}
                keyProvider="failover"
                keyStatus={keyStatus(llm.apiKeyConfigured)}
                cryptoReady={cryptoReady}
                keyLabel={keyMeta.label}
                keyPlaceholder={keyMeta.placeholder}
                keyHelper={keyMeta.helper}
                onProviderKeySave={(_provider, apiKey) => onLlmApiKeySave(apiKey)}
                onProviderKeyClear={() => onLlmApiKeyClear()}
                onProviderKeyTest={async () => {
                  const result = await testFailoverProviderConnection(llm.provider);
                  return { ok: result.ok, error: result.ok ? undefined : result.detail };
                }}
                onConnectionTest={(baseUrl) => testFailoverProviderConnection(llm.provider, baseUrl)}
              />

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <ModelSelect
                  id="failoverLlmDetectionModel"
                  label="Detection model"
                  value={llm.detectionModel}
                  catalog={failoverCatalog}
                  onChange={(detectionModel) => onLlmChange({ detectionModel })}
                />
                <ModelSelect
                  id="failoverLlmReviewModel"
                  label="Review model"
                  value={llm.reviewModel}
                  catalog={failoverCatalog}
                  onChange={(reviewModel) => onLlmChange({ reviewModel })}
                  inheritLabel="Same as detection"
                />
                <ModelSelect
                  id="failoverLlmVerificationModel"
                  label="Verification model"
                  value={llm.verificationModel}
                  catalog={failoverCatalog}
                  onChange={(verificationModel) => onLlmChange({ verificationModel })}
                  inheritLabel="Same as detection"
                />
                <ModelSelect
                  id="failoverLlmChaptersModel"
                  label="Chapters model"
                  value={llm.chaptersModel}
                  catalog={failoverCatalog}
                  onChange={(chaptersModel) => onLlmChange({ chaptersModel })}
                  inheritLabel="Same as detection"
                />
              </div>
            </>
          )}
        </div>

        <div className="pt-4 border-t border-border space-y-4">
          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <ToggleSwitch
                checked={whisper.enabled}
                onChange={(enabled) => onWhisperChange({ enabled })}
                ariaLabel="Enable transcription failover"
              />
              <span className="text-sm font-medium text-foreground">Transcription failover</span>
            </label>
            <p className="mt-2 text-sm text-muted-foreground ml-12">
              A standby transcriber used when the active one fails. It can run a different backend.
            </p>
          </div>

          {whisper.enabled && (
            <>
              <div>
                <label htmlFor="failoverWhisperBackend" className="block text-sm font-medium text-foreground mb-2">
                  Failover backend
                </label>
                <select
                  id="failoverWhisperBackend"
                  value={whisper.backend}
                  onChange={(e) => onWhisperChange({ backend: e.target.value as WhisperBackend })}
                  className={`w-full ${selectBase}`}
                >
                  <option value={WHISPER_BACKENDS.LOCAL}>Local (faster-whisper)</option>
                  <option value={WHISPER_BACKENDS.OPENAI_API}>Remote API (OpenAI-compatible)</option>
                </select>
              </div>

              {whisper.backend === WHISPER_BACKENDS.LOCAL ? (
                <div>
                  <label htmlFor="failoverWhisperModel" className="block text-sm font-medium text-foreground mb-2">
                    Local model
                  </label>
                  <input
                    type="text"
                    id="failoverWhisperModel"
                    value={whisper.model}
                    onChange={(e) => onWhisperChange({ model: e.target.value })}
                    placeholder="small"
                    spellCheck={false}
                    className={`w-full ${inputBase} placeholder:text-muted-foreground font-mono`}
                  />
                  <p className="mt-1 text-sm text-muted-foreground">faster-whisper model name, such as small or large-v3.</p>
                </div>
              ) : (
                <>
                  <div>
                    <label htmlFor="failoverWhisperApiBaseUrl" className="block text-sm font-medium text-foreground mb-2">
                      API base URL
                    </label>
                    <input
                      type="text"
                      id="failoverWhisperApiBaseUrl"
                      value={whisper.apiBaseUrl}
                      onChange={(e) => onWhisperChange({ apiBaseUrl: e.target.value })}
                      placeholder="http://host.docker.internal:8765/v1"
                      spellCheck={false}
                      className={`w-full ${inputBase} placeholder:text-muted-foreground font-mono`}
                    />
                    <ConnectionTestButton
                      key={`${whisper.apiBaseUrl}|${whisper.apiModel}|${skipFlacCompression}|${whisper.apiKeyConfigured}`}
                      onTest={() => testFailoverWhisperConnection(whisper.apiBaseUrl, whisper.apiModel, skipFlacCompression)}
                      busyHint="Sending a short audio sample, this can take up to 30 seconds"
                    />
                  </div>

                  <ProviderKeyField
                    provider="failover-whisper"
                    status={whisperKey}
                    cryptoReady={cryptoReady}
                    placeholder="(optional, leave blank if not required)"
                    label="API key"
                    onSave={(_provider, apiKey) => onWhisperApiKeySave(apiKey)}
                    onClear={() => onWhisperApiKeyClear()}
                    onTest={async () => {
                      const result = await testFailoverWhisperConnection(
                        whisper.apiBaseUrl, whisper.apiModel, skipFlacCompression);
                      return { ok: result.ok, error: result.ok ? undefined : result.detail };
                    }}
                  />

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="failoverWhisperApiModel" className="block text-sm font-medium text-foreground mb-2">
                        API model
                      </label>
                      <input
                        type="text"
                        id="failoverWhisperApiModel"
                        value={whisper.apiModel}
                        onChange={(e) => onWhisperChange({ apiModel: e.target.value })}
                        placeholder="whisper-1"
                        spellCheck={false}
                        className={`w-full ${inputBase} placeholder:text-muted-foreground font-mono`}
                      />
                    </div>
                    <div>
                      <label htmlFor="failoverWhisperApiTimeoutSeconds" className="block text-sm font-medium text-foreground mb-2">
                        API timeout (seconds)
                      </label>
                      <NumberInput
                        id="failoverWhisperApiTimeoutSeconds"
                        value={whisper.apiTimeoutSeconds}
                        min={30}
                        max={3600}
                        fallback={600}
                        step={1}
                        parse={(s) => parseInt(s, 10)}
                        onCommit={(apiTimeoutSeconds) => onWhisperChange({ apiTimeoutSeconds })}
                        className={`w-full ${inputBase}`}
                      />
                    </div>
                  </div>
                  <div>
                    <label htmlFor="failoverWhisperMaxAttempts" className="block text-sm font-medium text-foreground mb-2">
                      Max upload attempts
                    </label>
                    <DraftNumberInput
                      id="failoverWhisperMaxAttempts"
                      value={whisper.maxAttempts}
                      min={1}
                      max={10}
                      step={1}
                      fallback={null}
                      placeholder={String(activeWhisperMaxAttempts)}
                      parse={parseOptionalNumber}
                      onChange={(maxAttempts) => onWhisperChange({ maxAttempts })}
                      className={`w-full ${inputBase} placeholder:text-muted-foreground`}
                    />
                    <p className="mt-1 text-sm text-muted-foreground">
                      Includes the first upload. Leave blank to use the active transcriber's setting ({activeWhisperMaxAttempts} attempts).
                    </p>
                  </div>
                </>
              )}

              <div>
                <label htmlFor="failoverWhisperLanguage" className="block text-sm font-medium text-foreground mb-2">
                  Language
                </label>
                <input
                  type="text"
                  id="failoverWhisperLanguage"
                  value={whisper.language}
                  onChange={(e) => onWhisperChange({ language: e.target.value })}
                  placeholder="Same as the active transcriber"
                  spellCheck={false}
                  className={`w-full ${inputBase} placeholder:text-muted-foreground`}
                />
                <p className="mt-1 text-sm text-muted-foreground">Leave blank to use the active transcriber's language.</p>
              </div>
            </>
          )}
        </div>

        <div className="pt-4 border-t border-border">
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No failover events yet.</p>
          ) : (
            <details className="group">
              <summary className={`text-sm text-primary hover:underline cursor-pointer list-none rounded ${focusRing}`}>
                Recent events ({events.length})
              </summary>
              <ul className="mt-2 space-y-1.5">
                {events.map((e) => (
                  <li key={e.id} className="text-xs text-muted-foreground break-words">
                    {formatDateTime(e.createdAt)}: {eventText(e)}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>
    </CollapsibleSection>
  );
}

export default FailoverSection;
