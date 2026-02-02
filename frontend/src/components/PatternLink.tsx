import { Link } from 'react-router-dom';

interface PatternLinkProps {
  reason: string;
  className?: string;
}

export default function PatternLink({ reason, className = '' }: PatternLinkProps) {
  // Match "pattern #123" pattern
  const patternRegex = /\(pattern #(\d+)\)/g;
  const parts: (string | JSX.Element)[] = [];
  let lastIndex = 0;
  let match;

  while ((match = patternRegex.exec(reason)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(reason.slice(lastIndex, match.index));
    }
    // Add the linked pattern reference
    const patternId = match[1];
    parts.push(
      <Link
        key={match.index}
        to={`/patterns?id=${patternId}`}
        className="text-primary hover:underline"
        onClick={(e) => e.stopPropagation()}
      >
        (pattern #{patternId})
      </Link>
    );
    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < reason.length) {
    parts.push(reason.slice(lastIndex));
  }

  // If no matches, return plain text
  if (parts.length === 0) {
    return <span className={className}>{reason}</span>;
  }

  return <span className={className}>{parts}</span>;
}
