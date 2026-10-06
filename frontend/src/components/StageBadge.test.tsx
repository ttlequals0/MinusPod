import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StageBadge } from './StageBadge';
import { tint } from './badgeStyles';

describe('StageBadge', () => {
  it('labels transcript differential markers', () => {
    render(<StageBadge stage="transcript_differential" />);
    expect(screen.getByText('Transcript diff').className).toContain(tint.teal);
  });

  it('falls back to the raw stage name', () => {
    render(<StageBadge stage="some_future_stage" />);
    expect(screen.getByText('some_future_stage')).toBeTruthy();
  });
});
