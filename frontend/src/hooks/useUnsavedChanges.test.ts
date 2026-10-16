import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { hasUnsavedChanges, useUnsavedChanges } from './useUnsavedChanges';

describe('unsaved changes', () => {
  it('protects unload only while a draft exists', () => {
    const hook = renderHook(({ dirty }) => useUnsavedChanges(dirty), { initialProps: { dirty: false } });
    expect(hasUnsavedChanges()).toBe(false);
    hook.rerender({ dirty: true });
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    hook.rerender({ dirty: false });
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    expect(hasUnsavedChanges()).toBe(false);
  });

  it('keeps protection while another independently saved section is dirty', () => {
    const first = renderHook(() => useUnsavedChanges(true));
    const second = renderHook(() => useUnsavedChanges(true));
    first.unmount();
    expect(hasUnsavedChanges()).toBe(true);
    second.unmount();
    expect(hasUnsavedChanges()).toBe(false);
  });
});
