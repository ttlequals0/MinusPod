// Names the inherited global value only once it has loaded, so a wrong first value never flashes.
export const inheritLabel = (prefix: string, value: string | undefined): string =>
  value === undefined ? prefix : `${prefix} (${value})`;
