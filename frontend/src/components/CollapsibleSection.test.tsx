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

  it('ignores a pre-existing signal on mount, but applies the next fresh seq', async () => {
    // Mount fresh (e.g. behind an async settings load) while a bulk signal
    // already exists from an earlier click. The section must honor its own
    // defaultOpen, not retroactively apply the stale signal.
    const { rerender } = render(
      <SettingsBulkCollapseProvider value={{ seq: 1, open: true }}>
        <CollapsibleSection title="Test Section 6" storageKey="test-section-6" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );
    expect(screen.queryByText('content')).toBeNull();
    expect(JSON.parse(localStorage.getItem('test-section-6') ?? 'null')).toBe(false);

    // A genuinely new seq (a fresh click after mount) still applies normally.
    rerender(
      <SettingsBulkCollapseProvider value={{ seq: 2, open: true }}>
        <CollapsibleSection title="Test Section 6" storageKey="test-section-6" defaultOpen={false} unmountWhenClosed>
          <div>content</div>
        </CollapsibleSection>
      </SettingsBulkCollapseProvider>,
    );
    expect(await screen.findByText('content')).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('test-section-6') ?? 'null')).toBe(true);
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

// Exercises the real component (not the always-open stub LocalFeedPanel.test.tsx
// mocks CollapsibleSection with) -- that stub is why a validation message
// nested inside a defaultOpen=false section was never actually caught as
// invisible: the mock ignores open state entirely and renders children
// unconditionally. These tests assert the real collapse/forceOpen behavior.
describe('CollapsibleSection forceOpen', () => {
  it('mounts content when forceOpen is true even though the section defaults closed and was never clicked', () => {
    render(
      <CollapsibleSection title="Force Test" storageKey="force-test" defaultOpen={false} unmountWhenClosed forceOpen>
        <div>forced content</div>
      </CollapsibleSection>,
    );

    expect(screen.getByText('forced content')).toBeTruthy();
  });

  it('stays closed without forceOpen, proving the previous test is not a false positive', () => {
    render(
      <CollapsibleSection title="Force Test Control" storageKey="force-test-control" defaultOpen={false} unmountWhenClosed>
        <div>forced content</div>
      </CollapsibleSection>,
    );

    expect(screen.queryByText('forced content')).toBeNull();
  });

  it('does not persist forceOpen to storage, and hides content again once it clears', () => {
    const { rerender } = render(
      <CollapsibleSection title="Force Test 2" storageKey="force-test-2" defaultOpen={false} unmountWhenClosed forceOpen>
        <div>forced content</div>
      </CollapsibleSection>,
    );
    expect(screen.getByText('forced content')).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('force-test-2') ?? 'null')).toBe(false);

    rerender(
      <CollapsibleSection title="Force Test 2" storageKey="force-test-2" defaultOpen={false} unmountWhenClosed forceOpen={false}>
        <div>forced content</div>
      </CollapsibleSection>,
    );
    expect(screen.queryByText('forced content')).toBeNull();
  });
});
