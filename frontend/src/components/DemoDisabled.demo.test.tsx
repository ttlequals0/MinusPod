import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DemoDisabled } from './DemoDisabled';
import { DEMO_DOWNLOAD_TOOLTIP, DEMO_WRITE_TOOLTIP } from '../demo/copy';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

describe('DemoDisabled in demo', () => {
  test('disables the child and moves the tooltip to the wrapper', () => {
    render(<DemoDisabled><button title="Own title">Save</button></DemoDisabled>);
    const b = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    expect(b.getAttribute('aria-disabled')).toBe('true');
    expect(b.getAttribute('title')).toBeNull();
    expect(b.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });

  test('uses the title prop for the wrapper tooltip', () => {
    render(<DemoDisabled title={DEMO_DOWNLOAD_TOOLTIP}><button>Get</button></DemoDisabled>);
    expect(screen.getByRole('button', { name: 'Get' }).parentElement?.getAttribute('title')).toBe(DEMO_DOWNLOAD_TOOLTIP);
  });

  test('replaces an anchor with a disabled button that keeps its classes', () => {
    render(<DemoDisabled title={DEMO_DOWNLOAD_TOOLTIP}><a className="btn" href="/api/v1/x">Export</a></DemoDisabled>);
    expect(screen.queryByRole('link')).toBeNull();
    const b = screen.getByRole('button', { name: 'Export' }) as HTMLButtonElement;
    expect(b.disabled).toBe(true);
    expect(b.className).toBe('btn');
    expect(b.getAttribute('href')).toBeNull();
    expect(b.parentElement?.getAttribute('title')).toBe(DEMO_DOWNLOAD_TOOLTIP);
  });

  test('when=false passes the child through', () => {
    render(<DemoDisabled when={false}><button>Save</button></DemoDisabled>);
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
  });
});
