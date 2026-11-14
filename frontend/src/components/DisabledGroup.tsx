import { createContext, useContext } from 'react';

// Set by a disabled fieldset so custom controls that are not form elements can follow it.
export const DisabledGroupContext = createContext(false);

export function useGroupDisabled(): boolean {
  return useContext(DisabledGroupContext);
}
