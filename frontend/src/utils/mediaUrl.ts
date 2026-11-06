import { IS_DEMO } from '../demo/flag';
import { apiUrl } from '../urlPrefix';

export type MediaRef =
  | { kind: 'feedArtwork'; slug: string; apiArtworkUrl?: string | null }
  | { kind: 'episodeArtwork'; slug: string; episodeId: string }
  | { kind: 'audio'; url: string };

export function mediaUrl(ref: MediaRef): string | null {
  switch (ref.kind) {
    case 'feedArtwork':
      return IS_DEMO ? `/demo/artwork/feeds/${ref.slug}.jpg` : ref.apiArtworkUrl || apiUrl(`/feeds/${ref.slug}/artwork`);
    case 'episodeArtwork': {
      const id = encodeURIComponent(ref.episodeId);
      return IS_DEMO ? `/demo/artwork/episodes/${ref.slug}/${id}.jpg` : apiUrl(`/feeds/${ref.slug}/episodes/${id}/artwork`);
    }
    case 'audio':
      return IS_DEMO ? null : ref.url;
  }
}
