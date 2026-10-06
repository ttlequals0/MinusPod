import CollapsibleSection from '../../components/CollapsibleSection';
import ConfirmResetButton from './ConfirmResetButton';
import PromptField from './PromptField';

const OVERRIDE_HELP =
  'Optional. Appended to the prompt above at run time. Put {override} in a '
  + 'customized prompt to change where it goes.';

interface PromptsSectionProps {
  systemPrompt: string;
  verificationPrompt: string;
  chapterPrompt: string;
  systemPromptOverride: string;
  verificationPromptOverride: string;
  chapterPromptOverride: string;
  patternCleanupPrompt: string;
  onSystemPromptChange: (prompt: string) => void;
  onVerificationPromptChange: (prompt: string) => void;
  onChapterPromptChange: (prompt: string) => void;
  onSystemPromptOverrideChange: (prompt: string) => void;
  onVerificationPromptOverrideChange: (prompt: string) => void;
  onChapterPromptOverrideChange: (prompt: string) => void;
  onPatternCleanupPromptChange: (prompt: string) => void;
  onResetPrompts: () => void;
  resetIsPending: boolean;
  // Per-prompt reset (issue #626); overrides have no button of their own
  // since resetting the base prompt clears its override too.
  systemPromptIsDefault?: boolean;
  verificationPromptIsDefault?: boolean;
  chapterPromptIsDefault?: boolean;
  patternCleanupPromptIsDefault?: boolean;
  onResetSystemPrompt?: () => void;
  onResetVerificationPrompt?: () => void;
  onResetChapterPrompt?: () => void;
  onResetPatternCleanupPrompt?: () => void;
}

function PromptsSection({
  systemPrompt,
  verificationPrompt,
  chapterPrompt,
  systemPromptOverride,
  verificationPromptOverride,
  chapterPromptOverride,
  patternCleanupPrompt,
  onSystemPromptChange,
  onVerificationPromptChange,
  onChapterPromptChange,
  onSystemPromptOverrideChange,
  onVerificationPromptOverrideChange,
  onChapterPromptOverrideChange,
  onPatternCleanupPromptChange,
  onResetPrompts,
  resetIsPending,
  systemPromptIsDefault,
  verificationPromptIsDefault,
  chapterPromptIsDefault,
  patternCleanupPromptIsDefault,
  onResetSystemPrompt,
  onResetVerificationPrompt,
  onResetChapterPrompt,
  onResetPatternCleanupPrompt,
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
          onReset={onResetSystemPrompt}
          isDefault={systemPromptIsDefault}
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
          onReset={onResetVerificationPrompt}
          isDefault={verificationPromptIsDefault}
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
          onReset={onResetChapterPrompt}
          isDefault={chapterPromptIsDefault}
        />
        <PromptField
          id="chapterPromptOverride"
          label="Chapter Override"
          value={chapterPromptOverride}
          onChange={onChapterPromptOverrideChange}
          rows={3}
          helpText={OVERRIDE_HELP}
        />

        <PromptField
          id="patternCleanupPrompt"
          label="Pattern Cleanup Prompt"
          value={patternCleanupPrompt}
          onChange={onPatternCleanupPromptChange}
          helpText="Instructions for reviewing learned patterns in Experiments > Pattern Cleanup"
          onReset={onResetPatternCleanupPrompt}
          isDefault={patternCleanupPromptIsDefault}
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
