import { expect } from 'vitest';
import { LLM_DISABLED_REASON } from '../pages/settings/LlmGate';

// The control sits in a disabled fieldset whose description is the shared reason line.
export function expectGatedByLlm(control: Element) {
  const fieldset = control.closest('fieldset[disabled]');
  expect(fieldset).not.toBeNull();
  const noteId = fieldset!.getAttribute('aria-describedby');
  expect(noteId).toBeTruthy();
  expect(document.getElementById(noteId!)?.textContent).toContain(LLM_DISABLED_REASON);
}

export function expectNotGatedByLlm(control: Element) {
  expect(control.closest('fieldset[disabled]')).toBeNull();
}
