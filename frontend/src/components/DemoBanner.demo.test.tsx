import { expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DemoBanner } from './DemoBanner';
import { DEMO_BANNER } from '../demo/copy';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

test('renders the banner copy in demo builds', () => {
  render(<DemoBanner />);
  expect(screen.getByRole('note').textContent).toContain(DEMO_BANNER);
});
