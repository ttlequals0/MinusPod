import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { SystemOneProfile, SystemOneProfiles } from '../../api/types';
import SystemOneTunablesSection from './SystemOneTunablesSection';

const profile: SystemOneProfile = {
  detectionEnter: 0.7,
  detectionStay: 0.4,
  categoryPass: true,
  categoryContext: 2,
  defaultCategory: 'cross_promo',
  refineBoundaries: true,
  reviewEvidenceEnter: null,
  reviewChoiceEnter: null,
  reviewProgrammeVeto: 0.9,
  reviewBoundaryCapSeconds: 30,
  reviewContextSeconds: 60,
  requestDeadlineSeconds: 75,
  maxConcurrentOperations: 4,
  retryAfterMaxSeconds: 5,
  maxQuestionsPerRequest: null,
  maxRequestBytes: null,
  maxChoiceOptions: null,
};
const connections = { primary: 'typesafe', secondary: 'systemone-compatible' };
const profiles: SystemOneProfiles = {
  primary: { typesafe: { ...profile, maxChoiceOptions: 255 }, 'systemone-compatible': { ...profile } },
  secondary: { typesafe: { ...profile, maxChoiceOptions: 255 }, 'systemone-compatible': { ...profile } },
};
const defaults = structuredClone(profiles);
const isDefault = {
  primary: { typesafe: true, 'systemone-compatible': true },
  secondary: { typesafe: true, 'systemone-compatible': true },
};

describe('SystemOneTunablesSection', () => {
  beforeEach(() => localStorage.clear());

  it('retains an editable draft and renders the mutation error after a rejected save', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error('Save rejected'));
    render(<SystemOneTunablesSection connections={connections} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={onSave} pending={false} error="Save rejected" />);

    await user.click(screen.getByRole('button', { name: 'System One' }));
    const field = screen.getByLabelText('Detection confidence threshold', { selector: '#primary-typesafe-detectionEnter' }) as HTMLInputElement;
    await user.clear(field);
    await user.type(field, '0.8');
    await user.click(screen.getByRole('button', { name: 'Save System One tuning' }));

    expect(field.value).toBe('0.8');
    expect((await screen.findByRole('alert')).textContent).toContain('Save rejected');
    expect(onSave).toHaveBeenCalledOnce();
  });

  it('disables profile reset while another save is pending', async () => {
    render(<SystemOneTunablesSection connections={connections} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={vi.fn()} pending error={null} />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'System One' }));
    expect(screen.getAllByRole('button', { name: 'Reset profile' }).every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });

  it('saves concurrency changes only for the edited slot and provider', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SystemOneTunablesSection connections={connections} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={onSave} pending={false} error={null} />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    await user.selectOptions(screen.getByLabelText('Provider'), 'secondary');
    const field = screen.getByLabelText('Maximum concurrent operations', { selector: '#secondary-systemone-compatible-maxConcurrentOperations' });
    await user.clear(field);
    await user.type(field, '2');
    await user.click(screen.getByRole('button', { name: 'Save System One tuning' }));
    expect(onSave).toHaveBeenCalledWith({ systemOneTunables: { secondary: { 'systemone-compatible': { maxConcurrentOperations: 2 } } } });
    await user.selectOptions(screen.getByLabelText('Provider'), 'primary');
    expect((screen.getByLabelText('Maximum concurrent operations', { selector: '#primary-typesafe-maxConcurrentOperations' }) as HTMLInputElement).value).toBe('4');
  });

  it('keeps a cleared required field editable and saves fractional seconds', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SystemOneTunablesSection connections={connections} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={onSave} pending={false} error={null} />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    const field = screen.getByLabelText('Logical request deadline (seconds)', { selector: '#primary-typesafe-requestDeadlineSeconds' }) as HTMLInputElement;
    await user.clear(field);
    expect(field.value).toBe('');
    await user.type(field, '0.00025');
    await user.click(screen.getByRole('button', { name: 'Save System One tuning' }));
    expect(field.value).toBe('0.00025');
    expect(field.validity.valid).toBe(true);
    expect(onSave).toHaveBeenCalledWith({ systemOneTunables: { primary: { typesafe: { requestDeadlineSeconds: 0.00025 } } } });
  });

  it('saves null when a nullable threshold is cleared to inherit detection', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const customized = structuredClone(profiles);
    customized.secondary['systemone-compatible'].reviewEvidenceEnter = 0.72;
    render(<SystemOneTunablesSection connections={connections} profiles={customized} defaults={defaults} isDefault={isDefault} onSave={onSave} pending={false} error={null} />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    await user.selectOptions(screen.getByLabelText('Provider'), 'secondary');
    const field = screen.getByLabelText('Evidence enter threshold', { selector: '#secondary-systemone-compatible-reviewEvidenceEnter' }) as HTMLInputElement;
    await user.clear(field);
    await user.click(screen.getByRole('button', { name: 'Save System One tuning' }));
    expect(field.value).toBe('');
    expect(onSave).toHaveBeenCalledWith({ systemOneTunables: { secondary: { 'systemone-compatible': { reviewEvidenceEnter: null } } } });
  });

  it('keeps the reset draft visible and renders the error after a rejected reset', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(new Error('Reset rejected'));
    render(<SystemOneTunablesSection connections={connections} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={onSave} pending={false} error="Reset rejected" />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    await user.click(screen.getAllByRole('button', { name: 'Reset profile' })[0]);

    expect((await screen.findByRole('alert')).textContent).toContain('Reset rejected');
    expect(onSave).toHaveBeenCalledOnce();
  });

  it('restores drafts when switching slots and native provider types', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const props = { profiles, defaults, isDefault, onSave, pending: false, error: null };
    const { rerender } = render(<SystemOneTunablesSection {...props} connections={connections} />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    await user.clear(screen.getByLabelText('Detection confidence threshold'));
    await user.type(screen.getByLabelText('Detection confidence threshold'), '0.8');
    await user.selectOptions(screen.getByLabelText('Provider'), 'secondary');
    await user.clear(screen.getByLabelText('Maximum concurrent operations'));
    await user.type(screen.getByLabelText('Maximum concurrent operations'), '2');
    await user.selectOptions(screen.getByLabelText('Provider'), 'primary');
    expect((screen.getByLabelText('Detection confidence threshold') as HTMLInputElement).value).toBe('0.8');

    rerender(<SystemOneTunablesSection {...props} connections={{ ...connections, primary: 'systemone-compatible' }} />);
    await user.clear(screen.getByLabelText('Detection confidence threshold'));
    await user.type(screen.getByLabelText('Detection confidence threshold'), '0.6');
    rerender(<SystemOneTunablesSection {...props} connections={connections} />);
    expect((screen.getByLabelText('Detection confidence threshold') as HTMLInputElement).value).toBe('0.8');
    await user.click(screen.getByRole('button', { name: 'Save System One tuning' }));
    expect(onSave).toHaveBeenCalledWith({ systemOneTunables: {
      primary: { typesafe: { detectionEnter: 0.8 }, 'systemone-compatible': { detectionEnter: 0.6 } },
      secondary: { 'systemone-compatible': { maxConcurrentOperations: 2 } },
    } });
  });

  it('keeps a saved native Provider B profile editable when Provider A uses chat', async () => {
    render(<SystemOneTunablesSection connections={{ primary: 'anthropic', secondary: 'typesafe' }} profiles={profiles} defaults={defaults} isDefault={isDefault} onSave={vi.fn()} pending={false} error={null} />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'System One' }));
    expect(screen.getByLabelText('Detection confidence threshold', { selector: '#secondary-typesafe-detectionEnter' })).toBeTruthy();
    expect(screen.queryByLabelText('Provider')).toBeNull();
  });

  it('shows provider guidance without discarding a dormant draft', async () => {
    const user = userEvent.setup();
    const props = { profiles, defaults, isDefault, onSave: vi.fn(), pending: false, error: null };
    const { rerender } = render(<SystemOneTunablesSection {...props} connections={connections} />);
    await user.click(screen.getByRole('button', { name: 'System One' }));
    await user.clear(screen.getByLabelText('Detection confidence threshold'));
    await user.type(screen.getByLabelText('Detection confidence threshold'), '0.83');
    rerender(<SystemOneTunablesSection {...props} connections={{ primary: 'anthropic', secondary: 'openrouter' }} />);
    expect(screen.getByText(/Choose a System One provider in LLM Provider/)).toBeTruthy();
    expect(screen.queryByLabelText('Detection confidence threshold')).toBeNull();
    rerender(<SystemOneTunablesSection {...props} connections={connections} />);
    expect((screen.getByLabelText('Detection confidence threshold') as HTMLInputElement).value).toBe('0.83');
  });
});
