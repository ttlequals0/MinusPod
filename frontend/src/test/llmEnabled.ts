// Stand-in for hooks/useLlmEnabled so a section test sets the saved flag directly.
export const llmState = { enabled: true };

export function setLlmEnabled(enabled: boolean) {
  llmState.enabled = enabled;
}

export const llmEnabledMock = {
  useLlmEnabled: () => llmState.enabled,
  isLlmEnabled: () => llmState.enabled,
};
