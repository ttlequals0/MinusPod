import { ReactNode } from 'react';
import { Link } from 'react-router';

interface FeedArtworkLinkProps {
  slug: string;
  className: string;
  children: ReactNode;
}

// Decorative artwork thumbnail: hidden from keyboard and a11y tree so the
// card's title link stays the only accessible route to the feed.
function FeedArtworkLink({ slug, className, children }: FeedArtworkLinkProps) {
  return (
    <Link to={`/feeds/${slug}`} tabIndex={-1} aria-hidden="true" className={className}>
      {children}
    </Link>
  );
}

export default FeedArtworkLink;
