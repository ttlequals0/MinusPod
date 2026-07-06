/**
 * Component tests for SecuritySection (issue #461).
 *
 * The no-password warning must be state-aware:
 *   1. No password, no master passphrase: original full warning.
 *   2. No password, passphrase set: same severity, text acknowledges that
 *      the passphrase encrypts stored keys but does not restrict access.
 *   3. Password set: no warning at all.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SecuritySection from './SecuritySection';

vi.mock('../../api/providers', () => ({
  rotateMasterPassphrase: vi.fn(),
}));

vi.mock('../../api/auth', () => ({
  setPassword: vi.fn(),
  removePassword: vi.fn(),
}));

const NO_PASSWORD_TEXT = /This instance has no password, so anyone with network access has full control/;
const PASSPHRASE_TEXT = /The master passphrase encrypts stored API keys but does not restrict access to this app; anyone with network access still has full control\. Set a password below to protect it\./;

function renderSection(props: { isPasswordSet: boolean; cryptoReady?: boolean }) {
  return render(
    <SecuritySection
      isPasswordSet={props.isPasswordSet}
      cryptoReady={props.cryptoReady}
      logout={vi.fn()}
      refreshStatus={vi.fn()}
    />,
  );
}

describe('SecuritySection warning: no password, no passphrase', () => {
  it('shows the original full warning', () => {
    renderSection({ isPasswordSet: false, cryptoReady: false });

    expect(screen.queryByText(NO_PASSWORD_TEXT)).not.toBeNull();
    expect(screen.queryByText(PASSPHRASE_TEXT)).toBeNull();
  });
});

describe('SecuritySection warning: no password, passphrase set', () => {
  it('acknowledges the passphrase without weakening severity', () => {
    renderSection({ isPasswordSet: false, cryptoReady: true });

    const warning = screen.queryByText(PASSPHRASE_TEXT);
    expect(warning).not.toBeNull();
    expect(screen.queryByText(NO_PASSWORD_TEXT)).toBeNull();
    // Same amber severity styling as the original warning container.
    const container = warning!.closest('div');
    expect(container?.className).toContain('bg-yellow-500/10');
    expect(container?.className).toContain('border-yellow-500/20');
  });
});

describe('SecuritySection warning: password set', () => {
  it('shows no warning', () => {
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect(screen.queryByText(NO_PASSWORD_TEXT)).toBeNull();
    expect(screen.queryByText(PASSPHRASE_TEXT)).toBeNull();
    // Password-set state still renders its normal UI.
    expect(screen.queryByText('Current Password')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Logout' })).not.toBeNull();
  });
});
