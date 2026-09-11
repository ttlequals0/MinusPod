import { Component, Suspense, type ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChunkLoadRecovery, { ClearChunkLoadRecoveryMarkers } from './ChunkLoadRecovery';

class Throw extends Component<{ error: Error }> {
  render(): ReactNode { throw this.props.error; }
}

const reload = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  Object.defineProperty(window.location, 'reload', { configurable: true, value: reload });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

function renderFailure(error: Error) {
  return render(<ChunkLoadRecovery><Throw error={error} /></ChunkLoadRecovery>);
}

describe('ChunkLoadRecovery', () => {
  it('reloads once for a stale Chrome dynamic-import chunk', () => {
    const error = new Error('Failed to fetch dynamically imported module: https://example.test/ui/assets/Settings-old.js');
    renderFailure(error);
    expect(reload).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy();
    renderFailure(error);
    expect(reload).toHaveBeenCalledOnce();
  });

  it('allows a different later chunk to reload', () => {
    renderFailure(new Error('Loading chunk 4 failed: https://example.test/ui/assets/Settings-old.js'));
    renderFailure(new Error('Importing a module script failed: https://example.test/ui/assets/Settings-new.js'));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('does not reload generic fetch failures', () => {
    renderFailure(new Error('Failed to fetch'));
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByText('This page could not load. Reload to try again.')).toBeTruthy();
  });

  it('keeps manual reload available after automatic recovery is spent', () => {
    const error = new Error('Failed to fetch dynamically imported module: https://example.test/ui/assets/Settings-old.js');
    renderFailure(error);
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('clears recovery markers only after a route child mounts', () => {
    sessionStorage.setItem('minuspod:chunk-load-recovery:https://example.test/ui/assets/Settings-old.js', '1');
    render(<ClearChunkLoadRecoveryMarkers />);
    expect(sessionStorage.length).toBe(0);
  });

  it('does not clear a marker when a route child fails before mounting', () => {
    const key = 'minuspod:chunk-load-recovery:https://example.test/ui/assets/Settings-old.js';
    sessionStorage.setItem(key, '1');
    render(
      <ChunkLoadRecovery>
        <Suspense fallback={null}>
          <Throw error={new Error('Failed to fetch dynamically imported module: https://example.test/ui/assets/Settings-old.js')} />
          <ClearChunkLoadRecoveryMarkers />
        </Suspense>
      </ChunkLoadRecovery>,
    );
    expect(sessionStorage.getItem(key)).toBe('1');
  });
});
