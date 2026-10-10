import { LLM_PROVIDERS, LLM_PROVIDER_LABELS } from '../../api/types';
import type { Settings, SystemOneCredentialSlot, SystemOneProvider } from '../../api/types';

const SYSTEMONE_PROXY_MODELS = new Set(['jev-latest', 'jev-preview', 'typesafe/jev']);
const SYSTEMONE_SLOTS: SystemOneCredentialSlot[] = ['primary', 'secondary'];
const SYSTEMONE_PROVIDERS: SystemOneProvider[] = ['typesafe', 'systemone-compatible'];

// True once every slot x provider has tunables, defaults, and a known isDefault flag.
export function hasAllSystemOneProfiles(settings: Settings | undefined): settings is Settings {
  return SYSTEMONE_SLOTS.every((slot) => SYSTEMONE_PROVIDERS.every((provider) =>
    !!settings?.systemOneTunables?.[slot]?.[provider]
    && !!settings?.systemOneTunableDefaults?.[slot]?.[provider]
    && settings?.systemOneTunablesIsDefault?.[slot]?.[provider] !== undefined
  ));
}

export function isSystemOneRoute(provider: string, model: string): boolean {
  if (provider === LLM_PROVIDERS.TYPESAFE || provider === LLM_PROVIDERS.SYSTEMONE_COMPATIBLE) return true;
  return SYSTEMONE_PROXY_MODELS.has(model.trim().toLowerCase());
}

export function isSystemOneChapterRoute(settings: Settings | undefined): boolean {
  const secondaryUsable = settings?.secondaryProviderEnabled?.value && !!settings?.secondaryProvider?.value;
  const detectionSlot = settings?.detectionProvider?.value === 'secondary' && secondaryUsable ? 'secondary' : 'primary';
  const configuredSlot = settings?.chaptersProvider?.value;
  const chapterSlot = configuredSlot === 'secondary' ? (secondaryUsable ? 'secondary' : 'primary')
    : configuredSlot === 'primary' ? 'primary' : detectionSlot;
  const provider = chapterSlot === 'secondary' ? settings?.secondaryProvider?.value : settings?.llmProvider?.value;
  const model = settings?.chaptersModel?.value ?? settings?.claudeModel?.value ?? '';
  return isSystemOneRoute(provider ?? '', model);
}

export function systemOneRouteLabel(provider: string, model: string): string {
  const providerLabel = LLM_PROVIDER_LABELS[provider as keyof typeof LLM_PROVIDER_LABELS] || provider || 'Unknown provider';
  return `${providerLabel} / ${model || 'No model selected'}`;
}

export function effectiveReviewModels(
  provider: string,
  configuredModel: string,
  passModels: [string, string],
): string[] {
  if (!provider || provider === 'same_as_pass' || configuredModel === 'same_as_pass') {
    return passModels;
  }
  if (!configuredModel) return ['', ''];
  return [configuredModel, configuredModel];
}

export function effectiveStageModel(
  detectionModel: string,
  savedValue: string | null | undefined,
  currentValue: string,
): string {
  return savedValue === null && !currentValue ? detectionModel : currentValue;
}
