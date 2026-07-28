import { ReactNode } from 'react';

import { stripHtml } from '../utils/stripHtml';

/**
 * Renders a feed or episode description with its links intact: real anchors
 * whose text is often a person's name, and bare URLs in plain text.
 *
 * Nothing is injected as HTML. The input is parsed, then rebuilt as React
 * elements with only http, https, and mailto hrefs allowed through, so a
 * javascript: or data: URL in a publisher's feed cannot become a live link.
 */

const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);

// Trailing punctuation is almost always sentence punctuation, not part of the
// URL. Closing brackets are trimmed separately so "(see https://x.com)" works.
const URL_RE = /\bhttps?:\/\/[^\s<>"']+/g;
const TRAILING_PUNCT = /[.,;:!?)\]}>'"]+$/;

/** Tags whose boundaries are a line break in the rendered text. */
const BLOCK_TAGS = new Set([
  'P', 'DIV', 'LI', 'TR', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE',
]);

/** Tags whose boundary is a space, so a table row does not read "Cell1Cell2". */
const CELL_TAGS = new Set(['TD', 'TH']);

export function isSafeHref(href: string | null | undefined): boolean {
  if (!href) return false;
  // Relative and protocol-relative hrefs resolve against this app's origin and
  // render as broken links in a new tab, so they are not link material here.
  if (!/^(https?:|mailto:)/i.test(href.trim())) return false;
  try {
    // A base is required so relative hrefs resolve rather than throw.
    const url = new URL(href, 'https://invalid.example');
    return SAFE_PROTOCOLS.has(url.protocol);
  } catch {
    return false;
  }
}

function Anchor({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary hover:underline wrap-break-word"
    >
      {children}
    </a>
  );
}

/** Split plain text into text and anchors around any bare URLs. */
function linkifyText(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  URL_RE.lastIndex = 0;
  while ((match = URL_RE.exec(text)) !== null) {
    let url = match[0];
    const trailing = url.match(TRAILING_PUNCT)?.[0] ?? '';
    if (trailing) url = url.slice(0, -trailing.length);
    if (match.index > last) out.push(text.slice(last, match.index));
    if (isSafeHref(url)) {
      out.push(<Anchor key={`${keyPrefix}-${match.index}`} href={url}>{url}</Anchor>);
    } else {
      out.push(url);
    }
    if (trailing) out.push(trailing);
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function walk(node: Node, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  node.childNodes.forEach((child, i) => {
    const key = `${keyPrefix}-${i}`;
    if (child.nodeType === 3) {
      out.push(...linkifyText(child.textContent || '', key));
      return;
    }
    if (child.nodeType !== 1) return;

    const el = child as Element;
    if (el.tagName === 'A') {
      const href = el.getAttribute('href');
      const text = el.textContent || '';
      if (isSafeHref(href) && text.trim()) {
        out.push(<Anchor key={key} href={href as string}>{text}</Anchor>);
      } else if (text) {
        out.push(text);
      }
      return;
    }
    if (el.tagName === 'BR') {
      out.push('\n');
      return;
    }
    const isBlock = BLOCK_TAGS.has(el.tagName);
    if (isBlock) out.push('\n');
    if (el.tagName === 'LI') out.push('- ');
    out.push(...walk(el, key));
    if (isBlock) out.push('\n');
    else if (CELL_TAGS.has(el.tagName)) out.push(' ');
  });
  return out;
}

/**
 * Collapse the blank-line runs that adjacent block tags produce, so nested
 * markup does not open a description with a gap or double-space its paragraphs.
 */
function tidy(nodes: ReactNode[]): ReactNode[] {
  const out: ReactNode[] = [];
  for (const node of nodes) {
    if (typeof node !== 'string') {
      out.push(node);
      continue;
    }
    const prev = out[out.length - 1];
    if (typeof prev === 'string' && /\n\s*$/.test(prev) && /^\s*\n/.test(node)) {
      out[out.length - 1] = prev.replace(/\s+$/, '') + '\n' + node.replace(/^\s*\n\s*/, '');
      continue;
    }
    out.push(node);
  }
  while (out.length && typeof out[0] === 'string' && !out[0].trim()) out.shift();
  while (out.length && typeof out[out.length - 1] === 'string' && !(out[out.length - 1] as string).trim()) out.pop();
  if (out.length && typeof out[0] === 'string') out[0] = (out[0] as string).replace(/^\s*\n+/, '');
  return out;
}

interface RichTextProps {
  html?: string | null;
  className?: string;
}

function RichText({ html, className }: RichTextProps) {
  if (!html) return null;
  if (typeof DOMParser === 'undefined') {
    // No parser available: show the text rather than nothing, links inert.
    // stripHtml re-runs its strip until the string stops changing; a single
    // pass leaves "<scr<script>ipt>" as a live tag.
    return <span className={className}>{stripHtml(html)}</span>;
  }
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const nodes = tidy(walk(doc.body, 'rt'));
  // whitespace-pre-line keeps the paragraph breaks that block tags produce;
  // without it a description collapses into one run-on line.
  return <span className={`whitespace-pre-line ${className ?? ''}`.trim()}>{nodes}</span>;
}

export default RichText;
