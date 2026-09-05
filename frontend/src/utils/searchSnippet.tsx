import { ReactNode } from 'react';

const ENTITIES: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>' };

// The server escapes the snippet text and then wraps the matched words in <mark>, so a
// literal tag in indexed text arrives as text. Undo that escaping here; React escapes
// again on render, and nothing is passed through dangerouslySetInnerHTML.
function decodeEntities(text: string): string {
  return text.replace(/&(?:amp|lt|gt);/g, (entity) => ENTITIES[entity]);
}

export function renderSnippet(snippet: string): ReactNode[] {
  const parts = snippet.split(/(<mark>.*?<\/mark>)/g);
  return parts.map((part, i) => {
    const match = part.match(/^<mark>(.*?)<\/mark>$/);
    if (match) {
      return <mark key={i}>{decodeEntities(match[1])}</mark>;
    }
    return decodeEntities(part);
  });
}
