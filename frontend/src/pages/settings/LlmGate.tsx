import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { DisabledGroupContext } from '../../components/DisabledGroup';

export const LLM_DISABLED_REASON = 'LLM provider is set to None.';

// A stale page can still send a model-only request; the server answers 409 with this code.
export function isLlmDisabledError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409
    && (e.body as { code?: string } | undefined)?.code === 'llm_disabled';
}

export function LlmDisabledNote({ id, className = '' }: { id: string; className?: string }) {
  return <p id={id} className={`text-sm text-muted-foreground ${className}`}>{LLM_DISABLED_REASON}</p>;
}

interface LlmGateProps {
  disabled: boolean;
  id: string;
  className?: string;
  // An element elsewhere on the page that already states the reason; no second note is drawn.
  describedBy?: string;
  children: ReactNode;
}

// A disabled fieldset disables every control inside it for the keyboard and assistive technology.
export function LlmGate({ disabled, id, className = '', describedBy, children }: LlmGateProps) {
  const noteId = describedBy ?? `${id}-llm-disabled`;
  return (
    <fieldset
      disabled={disabled}
      aria-describedby={disabled ? noteId : undefined}
      className={`m-0 min-w-0 border-0 p-0 [&_:is(input,select,textarea):disabled]:opacity-50 ${className}`}
    >
      {disabled && !describedBy && <LlmDisabledNote id={noteId} />}
      <DisabledGroupContext.Provider value={disabled}>{children}</DisabledGroupContext.Provider>
    </fieldset>
  );
}
