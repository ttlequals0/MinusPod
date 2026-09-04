import { ReactNode } from 'react';

// Server sanitizes snippets with nh3, allowing only <mark>. Parses those tags
// into React nodes without dangerouslySetInnerHTML; everything else is plain
// text, auto-escaped by React.
export function renderSnippet(snippet: string): ReactNode[] {
  const parts = snippet.split(/(<mark>.*?<\/mark>)/g);
  return parts.map((part, i) => {
    const match = part.match(/^<mark>(.*?)<\/mark>$/);
    if (match) {
      return <mark key={i}>{match[1]}</mark>;
    }
    return part;
  });
}
