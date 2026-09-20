// Header magnifier reverted to a plain link (#717): a tap must focus a real
// input inside the gesture, which only the Dashboard field can do; the
// palette can no longer be the mobile entry point.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './Layout';

vi.mock('../context/ThemeContext', () => ({
  useTheme: () => ({ theme: 'light', toggleTheme: vi.fn() }),
}));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isPasswordSet: false, logout: vi.fn() }),
}));
vi.mock('./UpdateBanner', () => ({ default: () => null }));

function renderLayout(path = '/', queueCount = 0) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['processing-status'], {
    queueLength: queueCount,
    jobs: [],
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Layout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Layout header search', () => {
  it('links the magnifier to /search instead of opening the palette', () => {
    renderLayout();
    const link = screen.getByRole('link', { name: 'Search' });
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('/search');
  });
});

describe('Layout header hit targets', () => {
  it('gives the header controls a 44px box on phones', () => {
    renderLayout();
    const controls = [
      screen.getByRole('link', { name: 'Search' }),
      screen.getByRole('button', { name: 'Toggle theme' }),
      screen.getByRole('button', { name: 'Toggle menu' }),
    ];
    for (const el of controls) {
      // The rule sits on the interactive element itself, not on a wrapper.
      expect(el.className).toContain('min-h-11');
      expect(el.className).toContain('min-w-11');
    }
  });
});

describe('Layout queue badge', () => {
  it('keeps the count readable when Queue is selected', () => {
    renderLayout('/queue', 2);

    const badge = screen.getByText('2');
    expect(badge.classList.contains('bg-primary-foreground/20')).toBe(true);
    expect(badge.classList.contains('text-primary-foreground')).toBe(true);
  });
});
