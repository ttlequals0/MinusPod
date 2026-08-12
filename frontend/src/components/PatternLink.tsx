import type { ReactElement } from 'react';
import { Link } from 'react-router';
import { focusRing } from './fieldStyles';

interface PatternLinkProps {
  reason: string;
  className?: string;
}

export default function PatternLink({ reason, className = '' }: PatternLinkProps) {
  // The id is not always followed by a closing paren: a pattern marker's
  // reason carries the matched text after it, "(pattern #12, outro "...")".
  const patternRegex = /pattern #(\d+)/gi;
  const parts: (string | ReactElement)[] = [];
  let lastIndex = 0;
  let match;

  while ((match = patternRegex.exec(reason)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(reason.slice(lastIndex, match.index));
    }
    // Add the linked pattern reference
    const patternId = match[1];
    const label = match[0];
    parts.push(
      <Link
        key={match.index}
        to={`/patterns?id=${patternId}`}
        className={`text-primary hover:underline ${focusRing}`}
        onClick={(e) => e.stopPropagation()}
      >
        {label}
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
