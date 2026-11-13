import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { DisabledGroupContext } from '../../components/DisabledGroup';

export const LLM_DISABLED_REASON = 'LLM provider is set to None.';

// A stale page can still send a model-only request; the server answers 409 with this code.
export function isLlmDisabledError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409
    && (e.body as { code?: string } | undefined)?.code === 'llm_disabled';
}

export const NONE_SAVED_NOTE = `${LLM_DISABLED_REASON} No model calls are made. Ads are found by text patterns, audio fingerprints, audio cues and cross-fetch comparison. Text and audio matching need saved patterns. Mark ads to add them.`;

export const GENERATE_AS_AUTO_NOTE = `${LLM_DISABLED_REASON} Always generate behaves like Auto.`;

export function LlmDisabledNote({ id, className = '', text = LLM_DISABLED_REASON }: { id: string; className?: string; text?: string }) {
  return <p id={id} className={`text-sm text-muted-foreground ${className}`}>{text}</p>;
}

interface LlmGateProps {
  disabled: boolean;
  id: string;
  className?: string;
  // An element elsewhere on the page that already states the reason; no second note is drawn.
  describedBy?: string;
  // Replaces the default reason line.
  note?: string;
  children: ReactNode;
}

// A disabled fieldset disables every control inside it for the keyboard and assistive technology.
export function LlmGate({ disabled, id, className = '', describedBy, note, children }: LlmGateProps) {
  const noteId = describedBy ?? `${id}-llm-disabled`;
  return (
    <fieldset
      disabled={disabled}
      aria-describedby={disabled ? noteId : undefined}
      className={`min-w-0 border-0 p-0 [&_:is(input,select,textarea):disabled]:opacity-50 ${className}`}
    >
      {disabled && !describedBy && <LlmDisabledNote id={noteId} text={note} className="mb-2" />}
      <DisabledGroupContext.Provider value={disabled}>{children}</DisabledGroupContext.Provider>
    </fieldset>
  );
}
