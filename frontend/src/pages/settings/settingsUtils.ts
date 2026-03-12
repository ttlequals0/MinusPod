import type { ClaudeModel } from '../../api/types';

export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function formatDuration(seconds?: number): string {
  if (!seconds) return '0:00';
  const totalSecs = Math.floor(seconds);
  const hours = Math.floor(totalSecs / 3600);
  const minutes = Math.floor((totalSecs % 3600) / 60);
  const secs = totalSecs % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

export function formatCost(cost: number): string {
  return `$${cost.toFixed(2)}`;
}

export function formatStorage(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(1)} MB`;
}

export function formatModelLabel(model: ClaudeModel): string {
  if (model.inputCostPerMtok != null && model.outputCostPerMtok != null) {
    const fmtIn = model.inputCostPerMtok % 1 === 0
      ? model.inputCostPerMtok.toFixed(0) : model.inputCostPerMtok.toFixed(2);
    const fmtOut = model.outputCostPerMtok % 1 === 0
      ? model.outputCostPerMtok.toFixed(0) : model.outputCostPerMtok.toFixed(2);
    return `${model.name} ($${fmtIn} / $${fmtOut} per MTok)`;
  }
  return model.name;
}
