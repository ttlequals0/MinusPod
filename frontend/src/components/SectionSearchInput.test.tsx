import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SectionSearchInput from './SectionSearchInput';

function renderInput(value: string) {
  const onChange = vi.fn();
  const onClear = vi.fn();
  const utils = render(
    <SectionSearchInput
      value={value}
      onChange={onChange}
      onClear={onClear}
      placeholder="Search things"
      ariaLabel="Search things"
      clearLabel="Clear things search"
    />,
  );
  return { ...utils, onChange, onClear };
}

describe('SectionSearchInput', () => {
  it('renders the search icon, placeholder and aria-label', () => {
    const { container } = renderInput('');
    const input = screen.getByRole('textbox', { name: 'Search things' });
    expect(input.getAttribute('placeholder')).toBe('Search things');
    expect(container.querySelector('svg')).not.toBeNull();
  });

  it('hides the clear button while empty', () => {
    renderInput('');
    expect(screen.queryByRole('button', { name: 'Clear things search' })).toBeNull();
  });

  it('reports typed text through onChange', async () => {
    const { onChange } = renderInput('');
    await userEvent.setup().type(screen.getByRole('textbox'), 'a');
    expect(onChange).toHaveBeenCalledWith('a');
  });

  it('calls onClear from the X button', async () => {
    const { onClear } = renderInput('abc');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Clear things search' }));
    expect(onClear).toHaveBeenCalledOnce();
  });
});
