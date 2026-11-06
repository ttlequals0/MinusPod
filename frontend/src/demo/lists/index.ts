import type { Fixture } from '../fixtures';
import { awaitingApprovalHandler, heldHandler } from './approval';
import { changelogHandler } from './changelog';
import { detectionsHandler } from './detections';
import { downloadsHandler } from './downloads';
import { episodesHandler } from './episodes';
import { feedsHandler } from './feeds';
import { historyHandler } from './history';
import { patternsHandler } from './patterns';
import { processingHandler } from './processing';
import { episodeCostsHandler, modelUsageHandler } from './stats';
import { sponsorsHandler } from './sponsors';
import { suggestionsHandler } from './suggestions';

export type ListHandler = (url: URL, load: (key: string) => Promise<Fixture | null>) => Promise<Fixture | null>;

export const registry: Array<{ pattern: RegExp; handler: ListHandler }> = [
  { pattern: /^\/feeds$/, handler: feedsHandler },
  { pattern: /^\/feeds\/[^/]+\/episodes$/, handler: episodesHandler },
  { pattern: /^\/history$/, handler: historyHandler },
  { pattern: /^\/history\/downloads$/, handler: downloadsHandler },
  { pattern: /^\/detections$/, handler: detectionsHandler },
  { pattern: /^\/patterns$/, handler: patternsHandler },
  { pattern: /^\/sponsors$/, handler: sponsorsHandler },
  { pattern: /^\/stats\/model-usage$/, handler: modelUsageHandler },
  { pattern: /^\/stats\/episode-costs$/, handler: episodeCostsHandler },
  { pattern: /^\/episodes\/awaiting-approval$/, handler: awaitingApprovalHandler },
  { pattern: /^\/episodes\/held$/, handler: heldHandler },
  { pattern: /^\/episodes\/processing$/, handler: processingHandler },
  { pattern: /^\/patterns\/cleanup\/suggestions$/, handler: suggestionsHandler },
  { pattern: /^\/changelog$/, handler: changelogHandler },
];

export function matchList(pathname: string): ListHandler | null {
  for (const entry of registry) {
    if (entry.pattern.test(pathname)) return entry.handler;
  }
  return null;
}
