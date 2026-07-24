import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CollapsibleSection from './CollapsibleSection';
import { SettingsBulkCollapseProvider } from '../context/SettingsBulkCollapseContext';
import { SettingsSearchContext } from '../context/SettingsSearchContext';

beforeEach(() => {
  localStorage.clear();
});

describe('CollapsibleSection bulk expand/collapse', () => {
  it('opens a closed section on an expand-all signal and persists to localStorage', async () => {
    const { rerender } = render(
      <SettingsBulkCollapseProvider value={null}>
        <CollapsibleSection title="Test Section" storageKey="test-section" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );
    expect(screen.queryByText('content')).toBeNull();

    rerender(
      <SettingsBulkCollapseProvider value={{ seq: 1, open: true }}>
        <CollapsibleSection title="Test Section" storageKey="test-section" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );

    expect(await screen.findByText('content')).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('test-section') ?? 'null')).toBe(true);
  });

  it('closes an open section on a collapse-all signal', async () => {
    localStorage.setItem('test-section-2', JSON.stringify(true));
    const { rerender } = render(
      <SettingsBulkCollapseProvider value={null}>
        <CollapsibleSection title="Test Section 2" storageKey="test-section-2" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );
    expect(screen.getByText('content')).toBeTruthy();

    rerender(
      <SettingsBulkCollapseProvider value={{ seq: 1, open: false }}>
        <CollapsibleSection title="Test Section 2" storageKey="test-section-2" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );

    expect(screen.queryByText('content')).toBeNull();
    expect(JSON.parse(localStorage.getItem('test-section-2') ?? 'null')).toBe(false);
  });

  it('ignores the bulk signal while a search is active', async () => {
    const matchKeys = new Set<string>(); // active search, no matches
    const { rerender } = render(
      <SettingsBulkCollapseProvider value={null}>
        <SettingsSearchContext.Provider value={matchKeys}>
          <CollapsibleSection title="Test Section 3" storageKey="test-section-3" defaultOpen={false} unmountWhenClosed>
            <div>content</div>
          </CollapsibleSection>
        </SettingsSearchContext.Provider>
      </SettingsBulkCollapseProvider>,
    );

    rerender(
      <SettingsBulkCollapseProvider value={{ seq: 1, open: true }}>
        <SettingsSearchContext.Provider value={matchKeys}>
          <CollapsibleSection title="Test Section 3" storageKey="test-section-3" defaultOpen={false} unmountWhenClosed>
            <div>content</div>
          </CollapsibleSection>
        </SettingsSearchContext.Provider>
      </SettingsBulkCollapseProvider>,
    );

    // Signal is ignored during search: no state change from the open:true
    // signal, so the persisted value stays at its mount-time default and
    // expansion still follows the (empty) match set, not the bulk signal.
    expect(screen.queryByText('content')).toBeNull();
    expect(JSON.parse(localStorage.getItem('test-section-3') ?? 'null')).toBe(false);
  });

  it('leaves a section outside any provider unaffected (default null is inert)', async () => {
    render(
      <CollapsibleSection title="Test Section 4" storageKey="test-section-4" defaultOpen={false} unmountWhenClosed>
        <div>content</div>
      </CollapsibleSection>,
    );

    expect(screen.queryByText('content')).toBeNull();
    expect(JSON.parse(localStorage.getItem('test-section-4') ?? 'null')).toBe(false);
  });

  it('a manual click still toggles and persists normally alongside the bulk context', async () => {
    render(
      <SettingsBulkCollapseProvider value={null}>
        <CollapsibleSection title="Test Section 5" storageKey="test-section-5" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );

    await userEvent.click(screen.getByRole('button', { name: /test section 5/i }));
    expect(await screen.findByText('content')).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('test-section-5') ?? 'null')).toBe(true);
  });
});
