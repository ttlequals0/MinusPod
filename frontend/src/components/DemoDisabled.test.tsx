import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DemoDisabled, demoDisabledProps } from './DemoDisabled';

describe('DemoDisabled', () => {
  test('passes children through when not in demo', () => {
    render(<DemoDisabled><button>Save</button></DemoDisabled>);
    const b = screen.getByRole('button', { name: 'Save' });
    expect((b as HTMLButtonElement).disabled).toBe(false);
    expect(b.parentElement?.getAttribute('title')).toBeNull();
  });

  test('demoDisabledProps is empty when not in demo', () => {
    expect(demoDisabledProps()).toEqual({});
  });
});
