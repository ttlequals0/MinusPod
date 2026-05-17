import type { ImgHTMLAttributes } from 'react';

export const ARTWORK_FALLBACK_SVG =
  'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%239ca3af"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>';

type ArtworkProps = ImgHTMLAttributes<HTMLImageElement>;

function Artwork({ onError, ...rest }: ArtworkProps) {
  return (
    <img
      {...rest}
      onError={(e) => {
        (e.target as HTMLImageElement).src = ARTWORK_FALLBACK_SVG;
        onError?.(e);
      }}
    />
  );
}

export default Artwork;
