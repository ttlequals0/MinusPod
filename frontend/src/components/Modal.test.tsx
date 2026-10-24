import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

describe('Modal header and footer', () => {
  it('keeps header and footer outside the scrolling body', () => {
    render(
      <Modal ariaLabel="Demo" onClose={vi.fn()} header={<h2>Head</h2>} footer={<button>Foot</button>}>
        <p>Body</p>
      </Modal>,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog.hasAttribute('data-split')).toBe(true);
    const body = screen.getByText('Body').parentElement!;
    expect(body.className).toContain('overflow-y-auto');
    expect(body.contains(screen.getByText('Head'))).toBe(false);
    expect(body.contains(screen.getByText('Foot'))).toBe(false);
  });

  it('renders children directly without header or footer', () => {
    render(<Modal ariaLabel="Demo" onClose={vi.fn()}><p>Body</p></Modal>);
    expect(screen.getByRole('dialog').hasAttribute('data-split')).toBe(false);
  });
});
