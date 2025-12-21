export interface Feed {
  slug: string;
  title: string;
  sourceUrl: string;
  feedUrl: string;
  description?: string;
  artworkUrl?: string;
  episodeCount: number;
  processedCount?: number;
  lastRefreshed?: string;
  createdAt?: string;
  lastEpisodeDate?: string;
  networkId?: string;
  daiPlatform?: string;
  networkIdOverride?: string | null;
  audioAnalysisOverride?: boolean | null;
  autoProcessOverride?: boolean | null;
}

export interface Episode {
  id: string;
  title: string;
  description?: string;
  published: string;
  duration?: number;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  ad_count?: number;
}

export interface EpisodeDetail extends Episode {
  description?: string;
  originalUrl?: string;
  processedUrl?: string;
  transcript?: string;
  adMarkers?: AdSegment[];
  rejectedAdMarkers?: AdSegment[];
  corrections?: EpisodeCorrection[];
  originalDuration?: number;
  newDuration?: number;
  timeSaved?: number;
  fileSize?: number;
  adsRemovedFirstPass?: number;
  adsRemovedSecondPass?: number;
  firstPassPrompt?: string;
  firstPassResponse?: string;
  secondPassPrompt?: string;
  secondPassResponse?: string;
}

export interface AdValidation {
  decision: 'ACCEPT' | 'REVIEW' | 'REJECT';
  adjusted_confidence: number;
  original_confidence?: number;
  flags: string[];
  corrections?: string[];
}

export interface EpisodeCorrection {
  id: number;
  correction_type: 'confirm' | 'false_positive' | 'boundary_adjustment';
  original_bounds: { start: number; end: number };
  corrected_bounds?: { start: number; end: number };
  created_at: string;
}

export interface AdSegment {
  start: number;
  end: number;
  confidence: number;
  reason?: string;
  pass?: 1 | 2 | 'merged';
  validation?: AdValidation;
}

export interface SettingValue {
  value: string;
  isDefault: boolean;
}

export interface SettingValueBoolean {
  value: boolean;
  isDefault: boolean;
}

export interface Settings {
  systemPrompt: SettingValue;
  secondPassPrompt: SettingValue;
  claudeModel: SettingValue;
  secondPassModel: SettingValue;
  multiPassEnabled: SettingValueBoolean;
  whisperModel: SettingValue;
  audioAnalysisEnabled: SettingValueBoolean;
  autoProcessEnabled: SettingValueBoolean;
  audioBitrate: SettingValue;
  retentionPeriodMinutes: number;
  defaults: {
    systemPrompt: string;
    secondPassPrompt: string;
    claudeModel: string;
    secondPassModel: string;
    multiPassEnabled: boolean;
    whisperModel: string;
    audioAnalysisEnabled: boolean;
    autoProcessEnabled: boolean;
    audioBitrate: string;
  };
}

export interface UpdateSettingsPayload {
  systemPrompt?: string;
  secondPassPrompt?: string;
  claudeModel?: string;
  secondPassModel?: string;
  multiPassEnabled?: boolean;
  whisperModel?: string;
  audioAnalysisEnabled?: boolean;
  autoProcessEnabled?: boolean;
  audioBitrate?: string;
}

export interface ClaudeModel {
  id: string;
  name: string;
}

export interface WhisperModel {
  id: string;
  name: string;
  vram: string;
  speed: string;
  quality: string;
}

export interface SystemStatus {
  status: string;
  version: string;
  uptime: number;
  feeds: {
    total: number;
  };
  episodes: {
    total: number;
    byStatus: Record<string, number>;
  };
  storage: {
    usedMb: number;
    fileCount: number;
  };
  settings: {
    retentionPeriodMinutes: number;
    whisperModel: string;
    whisperDevice: string;
    baseUrl: string;
  };
  stats: {
    totalTimeSaved: number;
  };
}

export interface Sponsor {
  id: number;
  name: string;
  aliases: string[];
  category: string | null;
  is_active: boolean;
  created_at: string;
}

export interface SponsorNormalization {
  id: number;
  pattern: string;
  replacement: string;
  is_regex: boolean;
  priority: number;
  is_active: boolean;
  created_at: string;
}

export interface ProcessingHistoryEntry {
  id: number;
  podcastId: number;
  podcastSlug: string;
  podcastTitle: string;
  episodeId: string;
  episodeTitle: string;
  processedAt: string;
  processingDurationSeconds: number;
  status: 'completed' | 'failed';
  adsDetected: number;
  errorMessage?: string;
  reprocessNumber: number;
}

export interface ProcessingHistoryResponse {
  history: ProcessingHistoryEntry[];
  total: number;
  page: number;
  limit: number;
  totalPages: number;
}

export interface ProcessingHistoryStats {
  totalProcessed: number;
  completedCount: number;
  failedCount: number;
  totalAdsDetected: number;
  avgProcessingTime: number;
  totalProcessingTime: number;
}
