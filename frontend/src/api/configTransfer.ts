import { apiFileRequest, apiRequest } from './client';
import { downloadBlob } from './history';

export interface RuntimeConfigFeed {
  slug: string;
  feedType: 'subscribed' | 'local' | 'recents';
  settings: Record<string, unknown>;
}

export interface RuntimeConfigDocument {
  format: 'minuspod-runtime-config';
  formatVersion: number;
  appVersion: string;
  exportedAt: string;
  containsSecrets: boolean;
  settings: Record<string, unknown>;
  feeds: RuntimeConfigFeed[];
}

export interface ConfigImportPreview {
  previewToken: string;
  scope: 'everything' | 'global' | 'feeds';
  settings: string[];
  changedSettings: { kind: 'setting'; key: string; secret: boolean }[];
  addedFeeds: string[];
  updatedFeeds: string[];
  skippedUnknownSettings: string[];
  preservesTargetOnlyFeeds: boolean;
  warning: string;
  warnings: string[];
  selectedFeedSlugs: string[];
}

interface ImportRequest {
  document: RuntimeConfigDocument;
  scope: 'everything' | 'global' | 'feeds';
  selectedFeeds?: string[];
}

export async function downloadRuntimeConfig(): Promise<void> {
  const { blob, filename } = await apiFileRequest('/system/config-backup', {
    fallbackFilename: 'minuspod-runtime-config.json',
  });
  downloadBlob(blob, filename);
}

export async function previewConfigImport(
  request: ImportRequest,
): Promise<ConfigImportPreview> {
  return apiRequest<ConfigImportPreview>('/system/config-import/preview', {
    method: 'POST',
    body: request,
  });
}

export async function applyConfigImport(
  request: ImportRequest & { previewToken: string },
): Promise<{ message: string; addedFeeds: string[]; updatedFeeds: string[]; warnings: string[] }> {
  return apiRequest('/system/config-import', { method: 'POST', body: request });
}
