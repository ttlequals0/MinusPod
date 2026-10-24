import { Link } from 'react-router';
import { Plus } from 'lucide-react';
import { useDraftClose } from '../hooks/useDraftClose';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AdPattern, updatePattern, deletePattern, splitPattern } from '../api/patterns';
import { getSponsors, addSponsor } from '../api/sponsors';
import { getErrorMessage } from '../api/client';
import { ScopeBadge } from './ScopeBadge';
import { SegmentCategoryBadge } from './SegmentCategoryBadge';
import { Modal, dialogFooterBtn, dialogFooterGrid } from './Modal';
import Checkbox from './Checkbox';
import {
  SEGMENT_CATEGORIES,
  SEGMENT_CATEGORY_DESCRIPTIONS,
  SEGMENT_CATEGORY_LABELS,
  type SegmentCategory,
} from '../utils/segmentCategory';
import { btnBase, btnPrimary, btnOutline, btnDestructive, btnDestructiveOutline } from './buttonStyles';
import { focusRing, inputBase, labelBase, selectBase, textareaBase } from './fieldStyles';
import CloseButton from './CloseButton';
import StatTile from './StatTile';

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
  const saveLabel = updateMutation.isPending ? 'Saving...' : 'Save';

  return (
    <Modal ariaLabel="Pattern details" onClose={requestClose} panelClassName="max-w-2xl w-full"
      header={(
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
            <h2 className="text-lg font-semibold text-foreground">
              Pattern #{pattern.id}
            </h2>
            <ScopeBadge pattern={pattern} />
            <SegmentCategoryBadge category={pattern.category} />
          </div>
          <CloseButton onClick={requestClose} label="Close dialog" className="shrink-0" />
        </>
      )}
      footer={(
        <div className={`flex flex-nowrap justify-between gap-2 w-full ${showDeleteConfirm ? 'max-sm:grid max-sm:grid-cols-2 max-sm:gap-2' : dialogFooterGrid}`}>
          <div className="flex flex-nowrap items-center gap-1 sm:gap-2 max-sm:contents">
            {pattern.is_active && (
              <div className={`${showDeleteConfirm ? 'hidden sm:flex' : 'flex max-sm:contents'} flex-col gap-1`}>
                <button
                  onClick={() => splitMutation.mutate()}
                  disabled={!pattern.can_split || splitMutation.isPending}
                  aria-describedby={!pattern.can_split ? `split-reason-${pattern.id}` : undefined}
                  title={pattern.can_split ? undefined : 'No reliable ad boundary found.'}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnOutline}`}
                >
                  {splitLabel}
                </button>
                {!pattern.can_split && (
                  <span id={`split-reason-${pattern.id}`} className="hidden sm:block text-xs text-muted-foreground">
                    No reliable ad boundary found.
                  </span>
                )}
              </div>
            )}
            {showDeleteConfirm ? (
              <div className="flex flex-nowrap items-center gap-1 sm:gap-2 max-sm:contents">
                <span className="text-sm text-destructive max-sm:col-span-full">
                  <span className="sm:hidden">Delete?</span>
                  <span className="hidden sm:inline">Delete this pattern?</span>
                </span>
                <button
                  onClick={() => deleteMutation.mutate()}
                  disabled={deleteMutation.isPending}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnDestructive}`}
                >
                  {deleteMutation.isPending ? 'Deleting...' : 'Delete pattern'}
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnOutline}`}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className={`${dialogFooterBtn} max-sm:w-full ${btnDestructiveOutline}`}
              >
                Delete
              </button>
            )}
          </div>
          <div className={`flex flex-nowrap gap-1 sm:gap-2 ${showDeleteConfirm ? 'max-sm:hidden' : 'max-sm:contents'}`}>
            {isEditing ? (
              <>
                <button
                  onClick={() => setIsEditing(false)}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnOutline}`}
                >
                  Cancel
                </button>
                <button
                  onClick={() => updateMutation.mutate()}
                  disabled={updateMutation.isPending}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnPrimary}`}
                >
                  {saveLabel}
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={requestClose}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnOutline}`}
                >
                  Close
                </button>
                <button
                  onClick={() => setIsEditing(true)}
                  className={`${dialogFooterBtn} max-sm:w-full ${btnPrimary}`}
                >
                  Edit
                </button>
              </>
            )}
          </div>
        </div>
      )}
    >
      {/* Content */}
      <div className="p-4 space-y-4">
        {/* Stats Row */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(6rem,1fr))] gap-2 sm:gap-4">
          <StatTile label="Confirmations" value={pattern.confirmation_count} tone="success" />
          <StatTile label="False positives" value={pattern.false_positive_count} tone="destructive" />
          <StatTile label="Status" value={pattern.is_active ? 'Active' : 'Inactive'} />
        </div>

        {/* Editable Fields */}
        {isEditing ? (
          <div className="space-y-4">
            <div>
              <label htmlFor="pattern-sponsor" className={labelBase}>
                Sponsor
              </label>
              <div className="flex gap-2">
                <input
                  id="pattern-sponsor"
                  type="text"
                  list="sponsor-suggestions"
                  value={editedPattern.sponsor}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, sponsor: e.target.value }))}
                  placeholder="Start typing to see suggestions..."
                  className={`flex-1 min-w-0 ${inputBase}`}
                />
                {showAddSponsorButton && (
                  <button
                    type="button"
                    onClick={handleAddSponsor}
                    disabled={addSponsorMutation.isPending}
                    className={`${btnBase} ${btnPrimary}`}
                  >
                    <Plus className="w-4 h-4" aria-hidden="true" />
                    {addSponsorMutation.isPending ? 'Adding...' : 'Add sponsor'}
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
                  "{editedPattern.sponsor}" is not in the sponsor list. Select "Add sponsor" to add it.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="pattern-category" className={labelBase}>
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
              <label htmlFor="pattern-text-template" className={labelBase}>
                Text template
              </label>
              <textarea
                id="pattern-text-template"
                ref={textTemplateRef}
                value={editedPattern.text_template}
                onChange={(e) => setEditedPattern(prev => ({ ...prev, text_template: e.target.value }))}
                className={`w-full ${textareaBase} font-mono min-h-[160px] max-h-[400px] resize-y overflow-auto`}
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
                <label htmlFor="pattern-disabled-reason" className={labelBase}>
                  Disabled reason
                </label>
                <input
                  id="pattern-disabled-reason"
                  type="text"
                  value={editedPattern.disabled_reason}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, disabled_reason: e.target.value }))}
                  placeholder="Reason for disabling..."
                  className={`w-full ${inputBase}`}
                />
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Sponsor
              </div>
              <div className="text-foreground">
                {pattern.sponsor || '(Unknown)'}
              </div>
            </div>

            <div>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                Text template
              </div>
              <div className="text-sm text-foreground bg-secondary/50 rounded p-3 font-mono whitespace-pre-wrap min-h-[160px] max-h-[400px] overflow-auto">
                {pattern.text_template || '(No template)'}
              </div>
            </div>

            {pattern.intro_variants && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">
                  Intro variants
                </div>
                <div className="text-sm text-muted-foreground">
                  {pattern.intro_variants}
                </div>
              </div>
            )}

            {pattern.outro_variants && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">
                  Outro variants
                </div>
                <div className="text-sm text-muted-foreground">
                  {pattern.outro_variants}
                </div>
              </div>
            )}

            {pattern.disabled_reason && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1">
                  Disabled reason
                </div>
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
              <span className="text-muted-foreground">Last matched:</span>
              <span className="ml-2 text-foreground">{formatDate(pattern.last_matched_at)}</span>
            </div>
            {pattern.dai_platform && (
              <div>
                <span className="text-muted-foreground">DAI platform:</span>
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
                <span className="text-muted-foreground">Origin episode:</span>
                <span className="ml-2 text-foreground font-mono text-xs break-all">{pattern.created_from_episode_id}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Error Display */}
      {(updateMutation.isError || deleteMutation.isError || splitMutation.isError) && (
        <div className="px-4 pb-4">
          <div className="text-sm text-destructive bg-destructive/10 rounded p-2">
            {splitMutation.isError
              ? getErrorMessage(splitMutation.error, 'Could not split pattern.')
              : `${deleteMutation.isError ? 'Could not delete pattern.' : 'Could not save changes.'} Try again.`}
          </div>
        </div>
      )}
      {confirmation}
    </Modal>
  );
}

export default PatternDetailModal;
