import CollapsibleSection from '../../components/CollapsibleSection';

interface PromptsSectionProps {
  systemPrompt: string;
  verificationPrompt: string;
  onSystemPromptChange: (prompt: string) => void;
  onVerificationPromptChange: (prompt: string) => void;
  onResetPrompts: () => void;
  resetIsPending: boolean;
}

function PromptsSection({
  systemPrompt,
  verificationPrompt,
  onSystemPromptChange,
  onVerificationPromptChange,
  onResetPrompts,
  resetIsPending,
}: PromptsSectionProps) {
  return (
    <CollapsibleSection title="Prompts">
      <div className="space-y-6">
        <div>
          <label htmlFor="systemPrompt" className="block text-sm font-medium text-foreground mb-2">
            First Pass System Prompt
          </label>
          <textarea
            id="systemPrompt"
            value={systemPrompt}
            onChange={(e) => onSystemPromptChange(e.target.value)}
            rows={6}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm sm:rows-12"
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Instructions sent to the AI model for the initial ad detection pass
          </p>
        </div>

        <div>
          <label htmlFor="verificationPrompt" className="block text-sm font-medium text-foreground mb-2">
            Verification Prompt
          </label>
          <textarea
            id="verificationPrompt"
            value={verificationPrompt}
            onChange={(e) => onVerificationPromptChange(e.target.value)}
            rows={6}
            className="w-full px-4 py-2 rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-hidden focus:ring-2 focus:ring-ring font-mono text-sm sm:rows-12"
          />
          <p className="mt-1 text-sm text-muted-foreground">
            Instructions for the verification pass to detect ads missed by the first pass
          </p>
        </div>

        <button
          onClick={onResetPrompts}
          disabled={resetIsPending}
          className="px-4 py-2 rounded-lg bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
        >
          {resetIsPending ? 'Resetting...' : 'Reset Prompts to Default'}
        </button>
      </div>
    </CollapsibleSection>
  );
}

export default PromptsSection;
