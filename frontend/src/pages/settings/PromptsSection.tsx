import CollapsibleSection from '../../components/CollapsibleSection';
import PromptField from './PromptField';

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
        <PromptField
          id="systemPrompt"
          label="First Pass System Prompt"
          value={systemPrompt}
          onChange={onSystemPromptChange}
          helpText="Instructions sent to the AI model for the initial ad detection pass"
        />

        <PromptField
          id="verificationPrompt"
          label="Verification Prompt"
          value={verificationPrompt}
          onChange={onVerificationPromptChange}
          helpText="Instructions for the verification pass to detect ads missed by the first pass"
        />

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
