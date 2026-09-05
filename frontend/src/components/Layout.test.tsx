// Header magnifier reverted to a plain link (#717): a tap must focus a real
// input inside the gesture, which only the Dashboard field can do; the
// palette can no longer be the mobile entry point.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import Layout from './Layout';

vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }),
}));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isPasswordSet: false, logout: vi.fn() }),
}));
vi.mock('./UpdateBanner', () => ({ default: () => null }));

describe('Layout header search', () => {
  it('links the magnifier to /search instead of opening the palette', () => {
    render(
      <MemoryRouter>
        <Layout />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: 'Search' });
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('/search');
  });
});
