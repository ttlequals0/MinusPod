import { apiRequest } from './client';

export interface PodpingNodes {
  nodes: string[];
  defaults: string[];
}

export const podpingNodesQueryKey = ['podping', 'nodes'] as const;

export async function getPodpingNodes(): Promise<PodpingNodes> {
  return apiRequest<PodpingNodes>('/podping/nodes');
}

export async function updatePodpingNodes(nodes: string[]): Promise<PodpingNodes> {
  return apiRequest<PodpingNodes>('/podping/nodes', { method: 'PUT', body: { nodes } });
}

export async function resetPodpingNodes(): Promise<PodpingNodes> {
  return apiRequest<PodpingNodes>('/podping/nodes/reset', { method: 'POST' });
}
