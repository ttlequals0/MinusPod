import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DemoDisabled } from './DemoDisabled';

describe('DemoDisabled', () => {
  test('passes children through when not in demo', () => {
    render(<DemoDisabled><button>Save</button></DemoDisabled>);
    const b = screen.getByRole('button', { name: 'Save' });
    expect((b as HTMLButtonElement).disabled).toBe(false);
    expect(b.parentElement?.getAttribute('title')).toBeNull();
  });

  test('keeps the child title when not in demo', () => {
    render(<DemoDisabled><button title="Keep me">Save</button></DemoDisabled>);
    expect(screen.getByRole('button', { name: 'Save' }).getAttribute('title')).toBe('Keep me');
  });

  test('leaves anchors navigable when not in demo', () => {
    render(<DemoDisabled><a href="/x">Export</a></DemoDisabled>);
    expect(screen.getByRole('link', { name: 'Export' }).getAttribute('href')).toBe('/x');
  });
});
