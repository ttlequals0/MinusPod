import type { Feed, ModelPricingOverrides } from '../../api/types';
import { effectiveReviewModels, isSystemOneRoute } from './systemoneWarnings';

interface PricingRoute {
  provider: string;
  model: string;
}

interface PricingRoutes {
  detection: PricingRoute;
  verification: PricingRoute;
  verificationEnabled: boolean;
  reviewer: { enabled: boolean; provider: string; model: string };
  chapters: PricingRoute;
  cleanup?: PricingRoute;
  standby: {
    enabled: boolean;
    provider: string;
    detectionModel: string;
    verificationModel: string;
    reviewModel: string;
    chaptersModel: string;
  };
}

export function verificationPricingEnabled(
  skipSecondPass: boolean,
  feeds: Array<Pick<Feed, 'processingMode' | 'passthroughEnabled' | 'skipAdDetection' | 'skipSecondPass'>> = [],
): boolean {
  return !skipSecondPass || feeds.some((feed) => {
    const mode = feed.processingMode ?? (feed.passthroughEnabled ? 'passthrough'
      : feed.skipAdDetection ? 'skip_detection' : 'standard');
    return (mode === 'standard' || mode === 'keep_content')
      && !(feed.skipSecondPass ?? skipSecondPass);
  });
}

export function activePricingModelIds(routes: PricingRoutes): string[] {
  const ids = [routes.detection.model];
  const passModels: [string, string] = [routes.detection.model, routes.verification.model];
  if (routes.verificationEnabled) ids.push(routes.verification.model);
  if (routes.reviewer.enabled) {
    const reviewModels = effectiveReviewModels(routes.reviewer.provider, routes.reviewer.model, passModels);
    ids.push(reviewModels[0]);
    if (routes.verificationEnabled) ids.push(reviewModels[1]);
  }
  const chaptersAvailable = !!routes.chapters.model
    && !isSystemOneRoute(routes.chapters.provider, routes.chapters.model);
  const cleanupAvailable = !!routes.cleanup?.model
    && !isSystemOneRoute(routes.cleanup.provider, routes.cleanup.model);
  if (chaptersAvailable) ids.push(routes.chapters.model);
  if (cleanupAvailable) ids.push(routes.cleanup!.model);
  const standby = routes.standby;
  if (standby.enabled && standby.provider && standby.detectionModel
      && !isSystemOneRoute(standby.provider, standby.detectionModel)) {
    const standbyModels = [standby.detectionModel];
    if (routes.verificationEnabled) standbyModels.push(standby.verificationModel || standby.detectionModel);
    if (routes.reviewer.enabled) standbyModels.push(standby.reviewModel || standby.detectionModel);
    if (chaptersAvailable) standbyModels.push(standby.chaptersModel || standby.detectionModel);
    if (cleanupAvailable) standbyModels.push(standby.detectionModel);
    ids.push(...standbyModels.filter((model) => !isSystemOneRoute(standby.provider, model)));
  }
  return [...new Set(ids.filter(Boolean))];
}

function normalizedModelKey(modelId: string): string {
  return modelId.substring(modelId.indexOf('/') + 1)
    .replace(/:[a-z]+$/i, '')
    .replace(/-?20[2-3]\d-?\d{2}-?\d{2}$/, '')
    .toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function pricingOverrideKey(modelId: string, overrides: ModelPricingOverrides): string {
  if (Object.prototype.hasOwnProperty.call(overrides, modelId)) return modelId;
  const matchKey = normalizedModelKey(modelId);
  if (!matchKey) return modelId;
  const matches = Object.keys(overrides).filter((id) => normalizedModelKey(id) === matchKey);
  return matches.length === 1 ? matches[0] : modelId;
}
