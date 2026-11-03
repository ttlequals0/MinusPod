import { Fragment, type ReactNode } from 'react';
import type { ChangelogSection } from '../api/types';
import { Modal, dialogFooterBtn } from './Modal';
import { btnSecondary } from './buttonStyles';
import { tableLink } from './tableStyles';
import LoadingSpinner from './LoadingSpinner';

const REPO_URL = 'https://github.com/ttlequals0/MinusPod';
const INLINE_RE = /(`[^`]+`|\*\*[^*]+\*\*|#\d+)/g;
const footerLink = `${tableLink} inline-flex items-center pointer-coarse:min-h-11 text-sm`;

function renderInline(text: string): ReactNode[] {
  return text.split(INLINE_RE).map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 1) {
      return <code key={i} className="rounded bg-muted px-1 py-0.5 text-[0.85em] break-words">{part.slice(1, -1)}</code>;
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={i} className="font-semibold text-foreground">{part.slice(2, -2)}</strong>;
    }
    if (/^#\d+$/.test(part)) {
      return <a key={i} href={`${REPO_URL}/pull/${part.slice(1)}`} target="_blank" rel="noopener noreferrer" className={tableLink}>{part}</a>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function renderMarkdown(body: string): ReactNode[] {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (!bullets.length) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="list-disc space-y-1.5 pl-5 text-sm text-foreground">
        {items.map((b, i) => <li key={i} className="break-words">{renderInline(b)}</li>)}
      </ul>,
    );
  };
  for (const raw of body.split('\n')) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    const heading = /^###\s+(.*)$/.exec(line);
    const bullet = /^-\s+(.*)$/.exec(line);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flush();
    blocks.push(heading
      ? <p key={`h-${blocks.length}`} className="pt-1 text-sm font-semibold text-foreground">{renderInline(heading[1])}</p>
      : <p key={`p-${blocks.length}`} className="text-sm text-foreground break-words">{renderInline(line)}</p>);
  }
  flush();
  return blocks;
}

interface WhatsNewDialogProps {
  current: string;
  sections: ChangelogSection[] | undefined;
  isError?: boolean;
  onClose: () => void;
}

function WhatsNewDialog({ current, sections, isError = false, onClose }: WhatsNewDialogProps) {
  return (
    <Modal
      onClose={onClose}
      closeOnBackdrop
      ariaLabelledBy="whats-new-title"
      panelClassName="w-full max-w-lg max-h-[85dvh] flex flex-col text-card-foreground"
      header={<h2 id="whats-new-title" className="text-lg font-semibold">What's new in {current}</h2>}
      footer={(
        <div className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-center gap-x-4">
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className={footerLink}>MinusPod on GitHub</a>
            <a href={`${REPO_URL}/releases`} target="_blank" rel="noopener noreferrer" className={footerLink}>Release notes</a>
          </div>
          <button type="button" onClick={onClose} className={`${dialogFooterBtn} ${btnSecondary}`}>Close</button>
        </div>
      )}
    >
      <div className="space-y-6 p-4">
        {isError ? (
          <p role="alert" className="text-sm text-destructive">Could not load the changelog.</p>
        ) : !sections ? (
          <LoadingSpinner size="sm" />
        ) : sections.length === 0 ? (
          <p className="text-sm text-muted-foreground">No changelog entries are bundled with this build.</p>
        ) : sections.map((section) => (
          <section key={section.version} className="space-y-3">
            <h3 className="flex flex-wrap items-baseline gap-x-2 border-b border-border pb-2 text-xl font-semibold text-foreground">
              {section.version}
              {section.date && <span className="text-base font-normal text-muted-foreground">{section.date}</span>}
            </h3>
            {renderMarkdown(section.body)}
          </section>
        ))}
      </div>
    </Modal>
  );
}

export default WhatsNewDialog;
