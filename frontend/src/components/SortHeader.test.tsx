import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SortHeader } from './SortHeader';

type Field = 'name' | 'calls';

function renderHeader(sortField: Field, sortDirection: 'asc' | 'desc', onSort = vi.fn()) {
  render(
    <table>
      <thead>
        <tr>
          <SortHeader<Field>
            field="calls"
            label="Calls"
            sortField={sortField}
            sortDirection={sortDirection}
            onSort={onSort}
          />
        </tr>
      </thead>
    </table>,
  );
  return onSort;
}

describe('SortHeader accessibility', () => {
  it('exposes the sort state on the column', () => {
    renderHeader('calls', 'asc');
    expect(screen.getByRole('columnheader').getAttribute('aria-sort')).toBe('ascending');
  });

  it('reports descending and none for the other states', () => {
    const { unmount } = render(
      <table><thead><tr>
        <SortHeader<Field> field="calls" label="Calls" sortField="calls" sortDirection="desc" onSort={vi.fn()} />
      </tr></thead></table>,
    );
    expect(screen.getByRole('columnheader').getAttribute('aria-sort')).toBe('descending');
    unmount();

    renderHeader('name', 'asc');
    expect(screen.getByRole('columnheader').getAttribute('aria-sort')).toBe('none');
  });

  it('sorts from the keyboard through a real button', async () => {
    const user = userEvent.setup();
    const onSort = renderHeader('name', 'desc');
    const button = screen.getByRole('button', { name: 'Calls' });

    button.focus();
    await user.keyboard('{Enter}');
    expect(onSort).toHaveBeenCalledWith('calls');

    await user.keyboard(' ');
    expect(onSort).toHaveBeenCalledTimes(2);
  });

  it('keeps a focus ring on the control', () => {
    renderHeader('calls', 'asc');
    expect(screen.getByRole('button', { name: /Calls/ }).className).toMatch(/focus-visible:ring-2/);
  });
});
