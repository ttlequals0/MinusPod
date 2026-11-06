import { expect, test } from 'vitest';
import { render } from '@testing-library/react';
import { DemoExplainer } from './DemoExplainer';

test('renders nothing outside demo builds', () => {
  const { container } = render(<DemoExplainer title="Run logs">Body</DemoExplainer>);
  expect(container.firstChild).toBeNull();
});
