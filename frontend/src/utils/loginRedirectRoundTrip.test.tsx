import { render, screen } from '@testing-library/react';
import { MemoryRouter, Navigate, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { storeLoginRedirect, takeLoginRedirect } from './loginRedirect';

function Probe() {
  const l = useLocation();
  return <div data-testid="loc">{l.pathname + l.search + l.hash}</div>;
}

describe('login redirect round trip', () => {
  beforeEach(() => sessionStorage.clear());

  it('lands on the stored path, query and hash after the login Navigate', () => {
    storeLoginRedirect('/ui/settings', '?x=1', '#api-tokens');
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<Navigate to={takeLoginRedirect()} replace />} />
          <Route path="*" element={<Probe />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('loc').textContent).toBe('/settings?x=1#api-tokens');
  });
});
