import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Checkbox from './Checkbox';
import { DisabledGroupContext } from './DisabledGroup';

describe('Checkbox', () => {
  it('is unchanged outside a disabled group', () => {
    render(<Checkbox checked={false} onChange={() => {}} label="Refine" />);
    const label = screen.getByText('Refine').closest('label')!;
    expect(label.className).toContain('cursor-pointer');
    expect(label.className).not.toContain('opacity-50');
  });

  it('is dimmed with a default cursor inside a disabled group', () => {
    render(
      <DisabledGroupContext.Provider value>
        <Checkbox checked={false} onChange={() => {}} label="Refine" />
      </DisabledGroupContext.Provider>,
    );
    const label = screen.getByText('Refine').closest('label')!;
    expect(label.className).toContain('cursor-default');
    expect(label.className).toContain('opacity-50');
    expect(label.querySelector('input')!.disabled).toBe(true);
  });
});
