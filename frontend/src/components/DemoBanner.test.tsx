import { expect, test } from 'vitest';
import { render } from '@testing-library/react';
import { DemoBanner } from './DemoBanner';

test('renders nothing outside demo builds', () => {
  const { container } = render(<DemoBanner />);
  expect(container.firstChild).toBeNull();
});
