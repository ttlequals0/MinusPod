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
