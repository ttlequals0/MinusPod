import CollapsibleSection from '../../components/CollapsibleSection';
import ConfirmResetButton from './ConfirmResetButton';
import PromptField from './PromptField';

const OVERRIDE_HELP =
  'Optional. Added to this pass at run time; leave blank to use the default prompt '
  + 'unchanged. Put {override} in a customized prompt above to control where it goes.';

interface PromptsSectionProps {
  systemPrompt: string;
  verificationPrompt: string;
  chapterPrompt: string;
  systemPromptOverride: string;
  verificationPromptOverride: string;
  chapterPromptOverride: string;
  onSystemPromptChange: (prompt: string) => void;
  onVerificationPromptChange: (prompt: string) => void;
  onChapterPromptChange: (prompt: string) => void;
  onSystemPromptOverrideChange: (prompt: string) => void;
  onVerificationPromptOverrideChange: (prompt: string) => void;
  onChapterPromptOverrideChange: (prompt: string) => void;
  onResetPrompts: () => void;
  resetIsPending: boolean;
}

function PromptsSection({
  systemPrompt,
  verificationPrompt,
  chapterPrompt,
  systemPromptOverride,
  verificationPromptOverride,
  chapterPromptOverride,
  onSystemPromptChange,
  onVerificationPromptChange,
  onChapterPromptChange,
  onSystemPromptOverrideChange,
  onVerificationPromptOverrideChange,
  onChapterPromptOverrideChange,
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
          id="systemPromptOverride"
          label="First Pass Override"
          value={systemPromptOverride}
          onChange={onSystemPromptOverrideChange}
          rows={3}
          helpText={OVERRIDE_HELP}
        />

        <PromptField
          id="verificationPrompt"
          label="Verification Prompt"
          value={verificationPrompt}
          onChange={onVerificationPromptChange}
          helpText="Instructions for the verification pass to detect ads missed by the first pass"
        />
        <PromptField
          id="verificationPromptOverride"
          label="Verification Override"
          value={verificationPromptOverride}
          onChange={onVerificationPromptOverrideChange}
          rows={3}
          helpText={OVERRIDE_HELP}
        />

        <PromptField
          id="chapterPrompt"
          label="Chapter Prompt"
          value={chapterPrompt}
          onChange={onChapterPromptChange}
          helpText={'Instructions for chapter topic detection. Placeholders: {num_splits}, '
            + '{segment_start}, {segment_end}, {continuation_block}, {description_block}, '
            + '{hints_block}, {transcript}.'}
        />
        <PromptField
          id="chapterPromptOverride"
          label="Chapter Override"
          value={chapterPromptOverride}
          onChange={onChapterPromptOverrideChange}
          rows={3}
          helpText={OVERRIDE_HELP}
        />

        <ConfirmResetButton
          label="Reset Prompts to Default"
          isPending={resetIsPending}
          onConfirm={onResetPrompts}
        />
      </div>
    </CollapsibleSection>
  );
}

export default PromptsSection;
