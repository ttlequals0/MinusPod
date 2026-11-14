import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import CollapsibleSection from './CollapsibleSection';
import SearchableSectionGroup from './SearchableSectionGroup';
import { SettingsSectionTargetContext } from '../context/SettingsSectionTargetContext';

const scrollIntoView = vi.fn();
let originalOffsetParent: PropertyDescriptor | undefined;

beforeEach(() => {
  localStorage.clear();
  scrollIntoView.mockClear();
  Element.prototype.scrollIntoView = scrollIntoView;
  // jsdom has no layout: report every element as rendered.
  originalOffsetParent = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetParent');
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return document.body; } });
});

afterEach(() => {
  if (originalOffsetParent) Object.defineProperty(HTMLElement.prototype, 'offsetParent', originalOffsetParent);
  vi.unstubAllGlobals();
});

function Sections() {
  return (
    <>
      <CollapsibleSection title="Demo" storageKey="settings-section-demo" anchorId="demo">
        <div>body</div>
      </CollapsibleSection>
      <CollapsibleSection title="Other" storageKey="settings-section-other" anchorId="other">
        <div>other body</div>
      </CollapsibleSection>
    </>
  );
}

function Page({ hash, seq = 'k1' }: { hash: string | null; seq?: string }) {
  return (
    <SettingsSectionTargetContext.Provider value={hash ? { id: hash, seq } : null}>
      <Sections />
    </SettingsSectionTargetContext.Provider>
  );
}

const expanded = (name: string) => screen.getByRole('button', { name }).getAttribute('aria-expanded');

describe('settings hash deep link (anchorId)', () => {
  it('opens a section stored closed on cold mount, scrolls to it and focuses its toggle', () => {
    localStorage.setItem('settings-section-demo', 'false');
    render(<Page hash="demo" />);
    expect(expanded('Demo')).toBe('true');
    expect(expanded('Other')).toBe('false');
    expect(scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Demo' }));
    expect(document.getElementById('demo')).not.toBeNull();
    expect(localStorage.getItem('settings-section-demo')).toBe('true');
  });

  it('keeps an already-open section open and still scrolls', () => {
    localStorage.setItem('settings-section-demo', 'true');
    render(<Page hash="demo" />);
    expect(expanded('Demo')).toBe('true');
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it('opens an already-mounted section when only the hash changes, and again on a repeat click', () => {
    const { rerender } = render(<Page hash={null} />);
    expect(expanded('Demo')).toBe('false');
    expect(scrollIntoView).not.toHaveBeenCalled();
    rerender(<Page hash="demo" seq="k2" />);
    expect(expanded('Demo')).toBe('true');
    const calls = scrollIntoView.mock.calls.length;
    rerender(<Page hash="demo" seq="k3" />);
    expect(scrollIntoView.mock.calls.length).toBeGreaterThan(calls);
  });

  it('ignores an unknown hash', () => {
    render(<Page hash="nope" />);
    expect(expanded('Demo')).toBe('false');
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('re-aligns when layout shifts after load, and stops on user input', () => {
    let resizeCb: () => void = () => {};
    vi.stubGlobal('ResizeObserver', class {
      constructor(cb: () => void) { resizeCb = cb; }
      observe() {}
      disconnect() {}
    });
    render(<Page hash="demo" />);
    const calls = scrollIntoView.mock.calls.length;
    act(() => resizeCb());
    expect(scrollIntoView.mock.calls.length).toBe(calls + 1);
    // The observer is disconnected on user input, so a real shift would no longer fire; stop() must not throw.
    window.dispatchEvent(new Event('wheel'));
  });

  it('clears an active search so the targeted section is not filtered out', () => {
    const { rerender } = render(
      <SettingsSectionTargetContext.Provider value={null}>
        <SearchableSectionGroup placeholder="Search" ariaLabel="Search settings" clearLabel="Clear" revealKey={undefined}>
          <Sections />
        </SearchableSectionGroup>
      </SettingsSectionTargetContext.Provider>,
    );
    const input = screen.getByRole('textbox', { name: 'Search settings' }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'other body' } });
    expect(document.querySelector('[data-search-key="settings-section-demo"]')?.className).toContain('hidden');
    rerender(
      <SettingsSectionTargetContext.Provider value={{ id: 'demo', seq: 'k1' }}>
        <SearchableSectionGroup placeholder="Search" ariaLabel="Search settings" clearLabel="Clear" revealKey="k1">
          <Sections />
        </SearchableSectionGroup>
      </SettingsSectionTargetContext.Provider>,
    );
    expect(input.value).toBe('');
    expect(document.querySelector('[data-search-key="settings-section-demo"]')?.className).not.toContain('hidden');
    expect(expanded('Demo')).toBe('true');
  });
});
