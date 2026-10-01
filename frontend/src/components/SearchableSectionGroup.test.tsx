import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SearchableSectionGroup from './SearchableSectionGroup';
import CollapsibleSection from './CollapsibleSection';

beforeEach(() => {
  localStorage.clear();
});

describe('SearchableSectionGroup', () => {
  it('finds content inside a collapsed section that unmounts when closed', async () => {
    render(
      <SearchableSectionGroup placeholder="Search" ariaLabel="Search settings" clearLabel="Clear">
        <CollapsibleSection title="Charts" storageKey="charts" defaultOpen={false} unmountWhenClosed>
          <div>Retention window</div>
        </CollapsibleSection>
        <CollapsibleSection title="Queue" storageKey="queue" defaultOpen={false}>
          <div>Priority</div>
        </CollapsibleSection>
      </SearchableSectionGroup>,
    );

    // Paste the whole query: typing it would match the title on the first letter and mount the content.
    const user = userEvent.setup();
    await user.click(screen.getByRole('textbox', { name: 'Search settings' }));
    await user.paste('retention');

    expect(screen.getByRole('button', { name: 'Charts' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: 'Queue' }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText(/No settings match/)).toBeNull();
  });
});
