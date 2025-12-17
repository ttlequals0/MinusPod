import { apiRequest } from './client';
import { Sponsor, SponsorNormalization } from './types';

// Sponsor API

export async function getSponsors(): Promise<Sponsor[]> {
  const response = await apiRequest<{ sponsors: Sponsor[] }>('/sponsors');
  return response.sponsors;
}

export async function addSponsor(sponsor: {
  name: string;
  aliases?: string[];
  category?: string;
}): Promise<Sponsor> {
  return apiRequest<Sponsor>('/sponsors', {
    method: 'POST',
    body: sponsor,
  });
}

export async function getSponsor(id: number): Promise<Sponsor> {
  return apiRequest<Sponsor>(`/sponsors/${id}`);
}

export async function updateSponsor(
  id: number,
  updates: {
    name?: string;
    aliases?: string[];
    category?: string;
    is_active?: boolean;
  }
): Promise<Sponsor> {
  return apiRequest<Sponsor>(`/sponsors/${id}`, {
    method: 'PUT',
    body: updates,
  });
}

export async function deleteSponsor(id: number): Promise<void> {
  await apiRequest(`/sponsors/${id}`, { method: 'DELETE' });
}

// Normalization API

export async function getNormalizations(): Promise<SponsorNormalization[]> {
  const response = await apiRequest<{ normalizations: SponsorNormalization[] }>(
    '/sponsors/normalizations'
  );
  return response.normalizations;
}

export async function addNormalization(normalization: {
  pattern: string;
  replacement: string;
  is_regex?: boolean;
  priority?: number;
}): Promise<SponsorNormalization> {
  return apiRequest<SponsorNormalization>('/sponsors/normalizations', {
    method: 'POST',
    body: normalization,
  });
}

export async function updateNormalization(
  id: number,
  updates: {
    pattern?: string;
    replacement?: string;
    is_regex?: boolean;
    priority?: number;
    is_active?: boolean;
  }
): Promise<SponsorNormalization> {
  return apiRequest<SponsorNormalization>(`/sponsors/normalizations/${id}`, {
    method: 'PUT',
    body: updates,
  });
}

export async function deleteNormalization(id: number): Promise<void> {
  await apiRequest(`/sponsors/normalizations/${id}`, { method: 'DELETE' });
}
