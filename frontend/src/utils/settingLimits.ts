export interface SettingLimits {
  min: number;
  max: number;
  step?: number;
}

// The range a settings entry reports, else the fallback used for an older server or fixture.
export const limitsOf = (
  entry: { min?: number; max?: number; step?: number } | undefined,
  fallback: SettingLimits,
): SettingLimits => ({
  min: entry?.min ?? fallback.min,
  max: entry?.max ?? fallback.max,
  step: entry?.step ?? fallback.step,
});
