import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DropdownMenu from './DropdownMenu';

const items = [{ title: 'One', onClick: vi.fn() }];

describe('DropdownMenu', () => {
  it('closes an open menu when the trigger becomes disabled', async () => {
    const { rerender } = render(
      <DropdownMenu triggerLabel="Act" triggerClassName="" items={items} />);
    await userEvent.click(screen.getByRole('button', { name: 'Act' }));
    expect(screen.getByRole('menu')).toBeTruthy();
    rerender(<DropdownMenu triggerLabel="Act" triggerClassName="" items={items} disabled />);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
