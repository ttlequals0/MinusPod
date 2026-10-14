import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DateField from './DateField';

describe('DateField', () => {
  it('renders the label and the date input with min and max', () => {
    render(<DateField id="d" label="Since" value="2026-10-01" min="2026-09-01" max="2026-10-31" onChange={() => {}} />);
    const input = screen.getByLabelText('Since') as HTMLInputElement;
    expect(input.type).toBe('date');
    expect(input.value).toBe('2026-10-01');
    expect(input.min).toBe('2026-09-01');
    expect(input.max).toBe('2026-10-31');
  });

  it('carries the narrow-viewport overflow classes', () => {
    render(<DateField id="d" label="Since" value="" onChange={() => {}} />);
    const cls = screen.getByLabelText('Since').className;
    for (const c of ['max-sm:px-0', 'max-sm:text-base', '[&::-webkit-date-and-time-value]:text-left',
      '[&::-webkit-datetime-edit]:min-w-0', '[&::-webkit-datetime-edit]:p-0']) {
      expect(cls).toContain(c);
    }
  });

  it('calls onChange on change and again on blur with the typed value', () => {
    const onChange = vi.fn();
    render(<DateField id="d" label="Since" value="" onChange={onChange} />);
    const input = screen.getByLabelText('Since');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '2026-10-05' } });
    expect(onChange).toHaveBeenLastCalledWith('2026-10-05');
    onChange.mockClear();
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith('2026-10-05');
  });
});
