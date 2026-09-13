import { Component, type ReactNode, useEffect } from 'react';
import { btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';

const MARKER_PREFIX = 'minuspod:chunk-load-recovery:';

function assetUrl(error: unknown) {
  const match = String(error).match(/(?:https?:\/\/[^\s)]+|\/ui\/assets\/[^\s)]+)/i);
  if (match) return match[0];
  return document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src ?? window.location.href;
}

export function isStaleChunkError(error: unknown) {
  return /(chunkloaderror|loading chunk \d+ failed|failed to fetch dynamically imported module|importing a module script failed|dynamically imported module)/i.test(String(error));
}

function markerKey(error: unknown) {
  return `${MARKER_PREFIX}${assetUrl(error)}`;
}

function canReload(error: unknown) {
  try {
    const key = markerKey(error);
    if (sessionStorage.getItem(key)) return false;
    sessionStorage.setItem(key, '1');
    return true;
  } catch {
    return false;
  }
}

export function ClearChunkLoadRecoveryMarkers() {
  useEffect(() => {
    try {
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith(MARKER_PREFIX)) sessionStorage.removeItem(key);
      }
    } catch {
      // Storage can be unavailable in private browsing modes.
    }
  }, []);
  return null;
}

interface ChunkLoadRecoveryProps {
  children: ReactNode;
}

interface ChunkLoadRecoveryState {
  error: Error | null;
}

export default class ChunkLoadRecovery extends Component<ChunkLoadRecoveryProps, ChunkLoadRecoveryState> {
  state: ChunkLoadRecoveryState = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    if (isStaleChunkError(error) && canReload(error)) window.location.reload();
  }

  render() {
    if (!this.state.error) return this.props.children;
    const staleChunk = isStaleChunkError(this.state.error);
    return (
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <section className="max-w-xl rounded-lg border border-border bg-card p-6 text-card-foreground">
          <h1 className="text-lg font-semibold">Page update needed</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {staleChunk
              ? 'This page was updated while it was open. Reload to continue.'
              : 'This page could not load. Reload to try again.'}
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className={`mt-4 rounded-lg px-4 py-2 text-sm font-medium ${btnPrimary} ${focusRing}`}
          >
            Reload
          </button>
        </section>
      </main>
    );
  }
}
