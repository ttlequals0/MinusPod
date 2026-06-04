import { apiRequest } from './client';
import { Sponsor, SponsorNormalization, NormalizationCategory } from './types';

// Sponsor API

export async function getSponsors(includeInactive = false): Promise<Sponsor[]> {
  const qs = includeInactive ? '?include_inactive=true' : '';
  const response = await apiRequest<{ sponsors: Sponsor[] }>(`/sponsors${qs}`);
  return response.sponsors;
}

export async function addSponsor(sponsor: {
  name: string;
  aliases?: string[];
  category?: string;
}): Promise<{ message: string; id: number }> {
  return apiRequest<{ message: string; id: number }>('/sponsors', {
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

export async function deleteSponsor(id: number): Promise<{ unlinkedPatterns: number }> {
  return apiRequest<{ unlinkedPatterns: number }>(`/sponsors/${id}`, {
    method: 'DELETE',
  });
}

// Normalization API

export async function getNormalizations(): Promise<SponsorNormalization[]> {
  const response = await apiRequest<{ normalizations: SponsorNormalization[] }>(
    '/sponsors/normalizations'
  );
  return response.normalizations;
}

export async function addNormalization(normalization: {
  terms: string;
  canonical: string;
  category: NormalizationCategory;
}): Promise<SponsorNormalization> {
  return apiRequest<SponsorNormalization>('/sponsors/normalizations', {
    method: 'POST',
    body: normalization,
  });
}

export async function updateNormalization(
  id: number,
  updates: {
    terms?: string;
    canonical?: string;
    category?: NormalizationCategory;
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
