import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import ConnectionTestButton from './ConnectionTestButton';

vi.mock('../../demo/flag', () => ({ IS_DEMO: true }));

afterEach(cleanup);

describe('ConnectionTestButton in demo', () => {
  test('replaces the button with the provider tests explainer', () => {
    render(<ConnectionTestButton onTest={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Test connection' })).toBeNull();
    expect(screen.getByText('Provider tests')).toBeTruthy();
    expect(screen.getByText('Not available in the demo')).toBeTruthy();
  });
});
