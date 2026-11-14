import { IS_DEMO } from '../demo/flag';
import { apiUrl } from '../urlPrefix';

type MediaRef =
  | { kind: 'feedArtwork'; slug: string; apiArtworkUrl?: string | null }
  | { kind: 'episodeArtwork'; slug: string; episodeId: string }
  | { kind: 'episodeThumb'; slug: string; episodeId: string; apiThumbUrl?: string | null }
  | { kind: 'audio'; url: string };

export function mediaUrl(ref: MediaRef): string | null {
  switch (ref.kind) {
    case 'feedArtwork': {
      const slug = encodeURIComponent(ref.slug);
      return IS_DEMO ? `/demo/artwork/feeds/${slug}.jpg` : ref.apiArtworkUrl || apiUrl(`/feeds/${slug}/artwork`);
    }
    case 'episodeArtwork': {
      const slug = encodeURIComponent(ref.slug);
      const id = encodeURIComponent(ref.episodeId);
      return IS_DEMO ? `/demo/artwork/episodes/${slug}/${id}.jpg` : apiUrl(`/feeds/${slug}/episodes/${id}/artwork`);
    }
    case 'episodeThumb': {
      // The demo serves one static file per episode; the API hands out the thumbnail address itself.
      if (IS_DEMO) return mediaUrl({ kind: 'episodeArtwork', slug: ref.slug, episodeId: ref.episodeId });
      return ref.apiThumbUrl || null;
    }
    case 'audio':
      return IS_DEMO ? null : ref.url;
  }
}
