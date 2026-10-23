import { Check, Pencil, Scissors, Trash2, X } from 'lucide-react';
import { Link } from 'react-router';
import { useDraftClose } from '../hooks/useDraftClose';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AdPattern, updatePattern, deletePattern, splitPattern } from '../api/patterns';
import { getSponsors, addSponsor } from '../api/sponsors';
import { getErrorMessage } from '../api/client';
import { ScopeBadge } from './ScopeBadge';
import { SegmentCategoryBadge } from './SegmentCategoryBadge';
import { btnDestructive, btnOutline, btnPrimary, btnGhost, btnDestructiveOutline } from './buttonStyles';
import { Modal, dialogFooterIconBtn } from './Modal';
import Checkbox from './Checkbox';
import { focusRing, selectBase, inputBase } from './fieldStyles';
import {
  SEGMENT_CATEGORIES,
  SEGMENT_CATEGORY_DESCRIPTIONS,
  SEGMENT_CATEGORY_LABELS,
  type SegmentCategory,
} from '../utils/segmentCategory';

// 44px icon buttons below sm, text buttons from sm

interface PatternDetailModalProps {
  pattern: AdPattern;
  onClose: () => void;
  onSave: () => void;
}

function PatternDetailModal({ pattern, onClose, onSave }: PatternDetailModalProps) {
  const queryClient = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [editedPattern, setEditedPattern] = useState({
    text_template: pattern.text_template || '',
    sponsor: pattern.sponsor || '',
    category: (pattern.category || '') as SegmentCategory | '',
    is_active: pattern.is_active,
    disabled_reason: pattern.disabled_reason || '',
  });
  const dirty = isEditing && (editedPattern.text_template !== (pattern.text_template || '')
    || editedPattern.sponsor !== (pattern.sponsor || '') || editedPattern.category !== (pattern.category || '')
    || editedPattern.is_active !== pattern.is_active || editedPattern.disabled_reason !== (pattern.disabled_reason || ''));
  const { requestClose, confirmation } = useDraftClose(dirty, onClose);
  const textTemplateRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea to fit its content so view and edit render at the
  // same height. Clamped between min-h-[160px] and max-h-[400px] in CSS.
  useEffect(() => {
    if (!isEditing) return;
    const ta = textTemplateRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 400)}px`;
  }, [isEditing, editedPattern.text_template]);

  // Fetch sponsors for autocomplete
  const { data: sponsors } = useQuery({
    queryKey: ['sponsors'],
    queryFn: () => getSponsors(),
  });

  // Check if entered sponsor exists in list
  const sponsorExists = sponsors?.some(s =>
    s.name.toLowerCase() === editedPattern.sponsor.toLowerCase()
  );
  const showAddSponsorButton = editedPattern.sponsor.trim() && !sponsorExists;

  // Mutation to add new sponsor
  const addSponsorMutation = useMutation({
    mutationFn: (name: string) => addSponsor({ name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
    },
  });

  const handleAddSponsor = async () => {
    if (editedPattern.sponsor.trim()) {
      await addSponsorMutation.mutateAsync(editedPattern.sponsor.trim());
    }
  };

  const updateMutation = useMutation({
    mutationFn: () => updatePattern(pattern.id, {
      text_template: editedPattern.text_template || undefined,
      sponsor: editedPattern.sponsor || undefined,
      // '' means the user picked Uncategorized; null tells the API to clear.
      category: editedPattern.category === '' ? null : editedPattern.category,
      is_active: editedPattern.is_active,
      disabled_reason: editedPattern.disabled_reason || undefined,
    }),
    onSuccess: () => {
      setIsEditing(false);
      onSave();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deletePattern(pattern.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      onClose();
    },
  });

  const splitMutation = useMutation({
    mutationFn: () => splitPattern(pattern.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      onClose();
    },
  });

  // Local on purpose: full toLocaleString() (with seconds) differs from the
  // shared formatDateTime, which drops seconds.
  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
  };

  const splitLabel = splitMutation.isPending ? 'Splitting...' : 'Split';
  const saveLabel = updateMutation.isPending ? 'Saving...' : 'Save Changes';

  return (
    <Modal ariaLabel="Pattern details" onClose={requestClose} panelClassName="max-w-2xl w-full max-h-[90vh] overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
          <h2 className="text-lg font-semibold text-foreground">
            Pattern #{pattern.id}
          </h2>
          <ScopeBadge pattern={pattern} />
          <SegmentCategoryBadge category={pattern.category} />
        </div>
        <button
          onClick={requestClose}
          aria-label="Close dialog"
          className={`p-1 rounded inline-flex items-center justify-center shrink-0 ${btnGhost} ${focusRing}`}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Content */}
      <div className="p-4 space-y-4">
        {/* Stats Row */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(7rem,1fr))] gap-2 sm:gap-4">
          <div className="bg-secondary/50 rounded-lg p-2 sm:p-3 text-center">
            <div className="text-2xl font-bold text-success">
              {pattern.confirmation_count}
            </div>
            <div className="text-xs text-muted-foreground">Confirmations</div>
          </div>
          <div className="bg-secondary/50 rounded-lg p-2 sm:p-3 text-center">
            <div className="text-2xl font-bold text-destructive">
              {pattern.false_positive_count}
            </div>
            <div className="text-xs text-muted-foreground">False Positives</div>
          </div>
          <div className="bg-secondary/50 rounded-lg p-2 sm:p-3 text-center">
            <div className="text-lg font-medium text-foreground">
              {pattern.is_active ? 'Active' : 'Inactive'}
            </div>
            <div className="text-xs text-muted-foreground">Status</div>
          </div>
        </div>

        {/* Editable Fields */}
        {isEditing ? (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">
                Sponsor
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  list="sponsor-suggestions"
                  value={editedPattern.sponsor}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, sponsor: e.target.value }))}
                  placeholder="Start typing to see suggestions..."
                  className="flex-1 px-3 py-2 bg-secondary border border-border rounded text-sm"
                />
                {showAddSponsorButton && (
                  <button
                    type="button"
                    onClick={handleAddSponsor}
                    disabled={addSponsorMutation.isPending}
                    className={`px-3 py-2 text-xs ${btnPrimary} rounded disabled:opacity-50 whitespace-nowrap ${focusRing}`}
                  >
                    {addSponsorMutation.isPending ? 'Adding...' : 'Add New'}
                  </button>
                )}
              </div>
              <datalist id="sponsor-suggestions">
                {sponsors?.map((s) => (
                  <option key={s.id} value={s.name} />
                ))}
              </datalist>
              {showAddSponsorButton && (
                <p className="text-xs text-muted-foreground mt-1">
                  "{editedPattern.sponsor}" is not in the sponsor list. Click "Add New" to add it.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="pattern-category" className="block text-sm font-medium text-foreground mb-1">
                Category
              </label>
              <select
                id="pattern-category"
                value={editedPattern.category}
                onChange={(e) => setEditedPattern(prev => ({
                  ...prev, category: e.target.value as SegmentCategory | '',
                }))}
                className={`w-full ${selectBase}`}
                aria-describedby="pattern-category-help"
              >
                <option value="">Uncategorized</option>
                {SEGMENT_CATEGORIES.map((c) => (
                  <option key={c} value={c} title={SEGMENT_CATEGORY_DESCRIPTIONS[c]}>
                    {SEGMENT_CATEGORY_LABELS[c]}
                  </option>
                ))}
              </select>
              <p id="pattern-category-help" className="text-xs text-muted-foreground mt-1">
                Each feed's segment actions decide what happens to matches in
                this category. Uncategorized patterns count as Sponsor.
              </p>
            </div>

            <div>
              <label htmlFor="pattern-text-template" className="block text-sm font-medium text-foreground mb-1">
                Text Template
              </label>
              <textarea
                id="pattern-text-template"
                ref={textTemplateRef}
                value={editedPattern.text_template}
                onChange={(e) => setEditedPattern(prev => ({ ...prev, text_template: e.target.value }))}
                className={`w-full ${inputBase} font-mono min-h-[160px] max-h-[400px] resize-y overflow-auto`}
              />
            </div>

            <div className="flex items-center gap-4">
              <Checkbox
                checked={editedPattern.is_active}
                onChange={(v) => setEditedPattern(prev => ({ ...prev, is_active: v }))}
                label="Active"
              />
            </div>

            {!editedPattern.is_active && (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">
                  Disabled Reason
                </label>
                <input
                  type="text"
                  value={editedPattern.disabled_reason}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, disabled_reason: e.target.value }))}
                  placeholder="Reason for disabling..."
                  className="w-full px-3 py-2 bg-secondary border border-border rounded text-sm"
                />
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">
                Sponsor
              </label>
              <div className="text-foreground">
                {pattern.sponsor || '(Unknown)'}
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">
                Text Template
              </label>
              <div className="text-sm text-foreground bg-secondary/50 rounded p-3 font-mono whitespace-pre-wrap min-h-[160px] max-h-[400px] overflow-auto">
                {pattern.text_template || '(No template)'}
              </div>
            </div>

            {pattern.intro_variants && (
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">
                  Intro Variants
                </label>
                <div className="text-sm text-muted-foreground">
                  {pattern.intro_variants}
                </div>
              </div>
            )}

            {pattern.outro_variants && (
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">
                  Outro Variants
                </label>
                <div className="text-sm text-muted-foreground">
                  {pattern.outro_variants}
                </div>
              </div>
            )}

            {pattern.disabled_reason && (
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1">
                  Disabled Reason
                </label>
                <div className="text-sm text-destructive">
                  {pattern.disabled_reason}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Metadata */}
        <div className="border-t border-border pt-4 mt-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-muted-foreground">Created:</span>
              <span className="ml-2 text-foreground">{formatDate(pattern.created_at)}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Last Matched:</span>
              <span className="ml-2 text-foreground">{formatDate(pattern.last_matched_at)}</span>
            </div>
            {pattern.dai_platform && (
              <div>
                <span className="text-muted-foreground">DAI Platform:</span>
                <span className="ml-2 text-foreground break-words">{pattern.dai_platform}</span>
              </div>
            )}
            {pattern.podcast_slug && (
              <div>
                <span className="text-muted-foreground">Podcast:</span>
                <Link
                  to={`/feeds/${pattern.podcast_slug}`}
                  onClick={onClose}
                  className={`ml-2 text-primary hover:underline break-all ${focusRing}`}
                >
                  {pattern.podcast_slug}
                </Link>
              </div>
            )}
            {pattern.created_from_episode_id && (
              <div>
                <span className="text-muted-foreground">Origin Episode:</span>
                <span className="ml-2 text-foreground font-mono text-xs break-all">{pattern.created_from_episode_id}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex flex-nowrap justify-between gap-2 p-4 border-t border-border">
        <div className="flex flex-nowrap items-center gap-1 sm:gap-2">
          {pattern.is_active && (
            <div className={`${showDeleteConfirm ? 'hidden sm:flex' : 'flex'} flex-col gap-1`}>
              <button
                onClick={() => splitMutation.mutate()}
                disabled={!pattern.can_split || splitMutation.isPending}
                aria-describedby={!pattern.can_split ? `split-reason-${pattern.id}` : undefined}
                aria-label={splitLabel}
                title={pattern.can_split ? splitLabel : `${splitLabel}: No reliable ad boundary found.`}
                className={`${dialogFooterIconBtn} ${btnOutline} disabled:opacity-50`}
              >
                <Scissors className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">{splitLabel}</span>
              </button>
              {!pattern.can_split && (
                <span id={`split-reason-${pattern.id}`} className="hidden sm:block text-xs text-muted-foreground">
                  No reliable ad boundary found.
                </span>
              )}
            </div>
          )}
          {showDeleteConfirm ? (
            <div className="flex flex-nowrap items-center gap-1 sm:gap-2">
              <span className="text-sm text-destructive">
                <span className="sm:hidden">Delete?</span>
                <span className="hidden sm:inline">Delete this pattern?</span>
              </span>
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                aria-label={deleteMutation.isPending ? 'Deleting...' : 'Yes, Delete'}
                title={deleteMutation.isPending ? 'Deleting...' : 'Yes, delete'}
                className={`${dialogFooterIconBtn} ${btnDestructive} disabled:opacity-50`}
              >
                <Check className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">{deleteMutation.isPending ? 'Deleting...' : 'Yes, Delete'}</span>
              </button>
              <button
                onClick={() => setShowDeleteConfirm(false)}
                aria-label="Cancel"
                title="Cancel delete"
                className={`${dialogFooterIconBtn} ${btnOutline}`}
              >
                <X className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">Cancel</span>
              </button>
            </div>
          ) : (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              aria-label="Delete"
              title="Delete this pattern"
              className={`${dialogFooterIconBtn} ${btnDestructiveOutline}`}
            >
              <Trash2 className="h-5 w-5 sm:hidden" aria-hidden="true" />
              <span className="hidden sm:inline">Delete</span>
            </button>
          )}
        </div>
        <div className="flex flex-nowrap gap-1 sm:gap-2">
          {isEditing ? (
            <>
              <button
                onClick={() => setIsEditing(false)}
                aria-label="Cancel"
                title="Cancel editing"
                className={`${dialogFooterIconBtn} ${btnOutline}`}
              >
                <X className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">Cancel</span>
              </button>
              <button
                onClick={() => updateMutation.mutate()}
                disabled={updateMutation.isPending}
                aria-label={saveLabel}
                title={saveLabel}
                className={`${dialogFooterIconBtn} ${btnPrimary} disabled:opacity-50`}
              >
                <Check className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">{saveLabel}</span>
              </button>
            </>
          ) : (
            <>
              <button
                onClick={requestClose}
                aria-label="Close"
                title="Close"
                className={`${dialogFooterIconBtn} ${btnOutline}`}
              >
                <X className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">Close</span>
              </button>
              <button
                onClick={() => setIsEditing(true)}
                aria-label="Edit"
                title="Edit"
                className={`${dialogFooterIconBtn} ${btnPrimary}`}
              >
                <Pencil className="h-5 w-5 sm:hidden" aria-hidden="true" />
                <span className="hidden sm:inline">Edit</span>
              </button>
            </>
          )}
        </div>
      </div>

      {/* Error Display */}
      {(updateMutation.isError || deleteMutation.isError || splitMutation.isError) && (
        <div className="px-4 pb-4">
          <div className="text-sm text-destructive bg-destructive/10 rounded p-2">
            {splitMutation.isError
              ? getErrorMessage(splitMutation.error, 'Failed to split pattern.')
              : `${deleteMutation.isError ? 'Failed to delete pattern.' : 'Failed to save changes.'} Please try again.`}
          </div>
        </div>
      )}
      {confirmation}
    </Modal>
  );
}

export default PatternDetailModal;
