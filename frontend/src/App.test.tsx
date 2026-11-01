import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router';
import { sponsorsRedirectLoader } from './App';

function Where() {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

describe('sponsorsRedirectLoader', () => {
  it('sends /sponsors?q=x to /patterns?tab=sponsors&q=x', async () => {
    const router = createMemoryRouter(
      [
        { path: '/sponsors', loader: sponsorsRedirectLoader },
        { path: '/patterns', element: <Where /> },
      ],
      { initialEntries: ['/sponsors?q=x&tab=other'] },
    );
    render(<RouterProvider router={router} />);
    const text = (await screen.findByTestId('where')).textContent!;
    const params = new URLSearchParams(text.split('?')[1]);
    expect(text.startsWith('/patterns?')).toBe(true);
    expect(params.get('tab')).toBe('sponsors');
    expect(params.get('q')).toBe('x');
  });
});
