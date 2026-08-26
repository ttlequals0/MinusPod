/**
 * Tests for the Seed sponsors settings section: four independent toggles
 * controlling whether the known-sponsor list is included in each ad-review
 * prompt (detection, verification, reviewer, resurrect).
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SeedSponsorsSection from './SeedSponsorsSection';

type SeedSponsorsKey =
  | 'seedSponsorsDetection'
  | 'seedSponsorsVerification'
  | 'seedSponsorsReviewer'
  | 'seedSponsorsResurrect';

function Harness({
  onChange,
}: {
  onChange: (key: SeedSponsorsKey, value: boolean) => void;
}) {
  const [detection, setDetection] = useState(true);
  const [verification, setVerification] = useState(true);
  const [reviewer, setReviewer] = useState(true);
  const [resurrect, setResurrect] = useState(true);

  const handleChange = (key: SeedSponsorsKey, value: boolean) => {
    if (key === 'seedSponsorsDetection') setDetection(value);
    if (key === 'seedSponsorsVerification') setVerification(value);
    if (key === 'seedSponsorsReviewer') setReviewer(value);
    if (key === 'seedSponsorsResurrect') setResurrect(value);
    onChange(key, value);
  };

  return (
    <SeedSponsorsSection
      detection={detection}
      verification={verification}
      reviewer={reviewer}
      resurrect={resurrect}
      onChange={handleChange}
    />
  );
}

describe('SeedSponsorsSection: rendering', () => {
  it('renders four labeled switches, all on by default', () => {
    render(<Harness onChange={() => {}} />);
    const detection = screen.getByRole('switch', { name: 'Detection' });
    const verification = screen.getByRole('switch', { name: 'Verification' });
    const reviewer = screen.getByRole('switch', { name: 'Reviewer' });
    const resurrect = screen.getByRole('switch', { name: 'Resurrect' });

    expect(detection.getAttribute('aria-checked')).toBe('true');
    expect(verification.getAttribute('aria-checked')).toBe('true');
    expect(reviewer.getAttribute('aria-checked')).toBe('true');
    expect(resurrect.getAttribute('aria-checked')).toBe('true');
  });

  it('shows the section title', () => {
    render(<Harness onChange={() => {}} />);
    expect(screen.getByText('Seed sponsors')).toBeTruthy();
  });

  it('shows the section subtitle once expanded', async () => {
    render(<Harness onChange={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByText('Seed sponsors'));
    expect(
      screen.getByText('Choose which prompts see the known-sponsor list. All on by default.'),
    ).toBeTruthy();
  });

  it('does not call onChange on initial render or on expanding the section', async () => {
    const calls: Array<[SeedSponsorsKey, boolean]> = [];
    render(<Harness onChange={(key, value) => calls.push([key, value])} />);
    expect(calls).toEqual([]);

    const user = userEvent.setup();
    await user.click(screen.getByText('Seed sponsors'));
    expect(calls).toEqual([]);
  });
});

describe('SeedSponsorsSection: reviewer toggle', () => {
  it('calls onChange with seedSponsorsReviewer and nothing else when clicked', async () => {
    const calls: Array<[SeedSponsorsKey, boolean]> = [];
    render(<Harness onChange={(key, value) => calls.push([key, value])} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Reviewer' }));

    expect(calls).toEqual([['seedSponsorsReviewer', false]]);
  });

  it('does not change the other three switches', async () => {
    render(<Harness onChange={() => {}} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Reviewer' }));

    expect(screen.getByRole('switch', { name: 'Detection' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Verification' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Resurrect' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Reviewer' }).getAttribute('aria-checked')).toBe('false');
  });
});

describe('SeedSponsorsSection: other toggles', () => {
  it('calls onChange with seedSponsorsDetection when the detection switch is clicked', async () => {
    const calls: Array<[SeedSponsorsKey, boolean]> = [];
    render(<Harness onChange={(key, value) => calls.push([key, value])} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Detection' }));

    expect(calls).toEqual([['seedSponsorsDetection', false]]);
  });

  it('calls onChange with seedSponsorsVerification when the verification switch is clicked', async () => {
    const calls: Array<[SeedSponsorsKey, boolean]> = [];
    render(<Harness onChange={(key, value) => calls.push([key, value])} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Verification' }));

    expect(calls).toEqual([['seedSponsorsVerification', false]]);
  });

  it('calls onChange with seedSponsorsResurrect when the resurrect switch is clicked', async () => {
    const calls: Array<[SeedSponsorsKey, boolean]> = [];
    render(<Harness onChange={(key, value) => calls.push([key, value])} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Resurrect' }));

    expect(calls).toEqual([['seedSponsorsResurrect', false]]);
  });
});
