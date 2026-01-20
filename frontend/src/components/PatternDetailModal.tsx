import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AdPattern, updatePattern, deletePattern } from '../api/patterns';
import { getSponsors, addSponsor } from '../api/sponsors';

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
    is_active: pattern.is_active,
    disabled_reason: pattern.disabled_reason || '',
  });

  // Fetch sponsors for autocomplete
  const { data: sponsors } = useQuery({
    queryKey: ['sponsors'],
    queryFn: getSponsors,
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

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
  };

  const getScopeBadge = () => {
    if (pattern.scope === 'global') {
      return <span className="px-2 py-0.5 text-xs rounded bg-blue-500/20 text-blue-600 dark:text-blue-400">Global</span>;
    } else if (pattern.scope === 'network') {
      return <span className="px-2 py-0.5 text-xs rounded bg-purple-500/20 text-purple-600 dark:text-purple-400">Network: {pattern.network_id}</span>;
    } else if (pattern.scope === 'podcast') {
      return <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">{pattern.podcast_name || 'Podcast'}</span>;
    }
    return null;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-foreground">
              Pattern #{pattern.id}
            </h2>
            {getScopeBadge()}
          </div>
          <button
            onClick={onClose}
            className="p-1 text-muted-foreground hover:text-foreground"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Stats Row */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-secondary/50 rounded-lg p-3 text-center">
              <div className="text-2xl font-bold text-green-600 dark:text-green-400">
                {pattern.confirmation_count}
              </div>
              <div className="text-xs text-muted-foreground">Confirmations</div>
            </div>
            <div className="bg-secondary/50 rounded-lg p-3 text-center">
              <div className="text-2xl font-bold text-red-600 dark:text-red-400">
                {pattern.false_positive_count}
              </div>
              <div className="text-xs text-muted-foreground">False Positives</div>
            </div>
            <div className="bg-secondary/50 rounded-lg p-3 text-center">
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
                      className="px-3 py-2 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50 whitespace-nowrap"
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
                <label className="block text-sm font-medium text-foreground mb-1">
                  Text Template
                </label>
                <textarea
                  value={editedPattern.text_template}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, text_template: e.target.value }))}
                  rows={4}
                  className="w-full px-3 py-2 bg-secondary border border-border rounded text-sm font-mono"
                />
              </div>

              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editedPattern.is_active}
                    onChange={(e) => setEditedPattern(prev => ({ ...prev, is_active: e.target.checked }))}
                    className="rounded"
                  />
                  <span className="text-sm text-foreground">Active</span>
                </label>
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
                <div className="text-sm text-foreground bg-secondary/50 rounded p-3 font-mono whitespace-pre-wrap">
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
                  <div className="text-sm text-red-600 dark:text-red-400">
                    {pattern.disabled_reason}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Metadata */}
          <div className="border-t border-border pt-4 mt-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
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
                  <span className="ml-2 text-foreground">{pattern.dai_platform}</span>
                </div>
              )}
              {pattern.podcast_slug && (
                <div>
                  <span className="text-muted-foreground">Podcast:</span>
                  <a
                    href={`/feeds/${pattern.podcast_slug}/episodes`}
                    className="ml-2 text-primary hover:underline"
                  >
                    {pattern.podcast_slug}
                  </a>
                </div>
              )}
              {pattern.created_from_episode_id && (
                <div>
                  <span className="text-muted-foreground">Origin Episode:</span>
                  <span className="ml-2 text-foreground font-mono text-xs">{pattern.created_from_episode_id}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-between gap-2 p-4 border-t border-border">
          <div>
            {showDeleteConfirm ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-destructive">Delete this pattern?</span>
                <button
                  onClick={() => deleteMutation.mutate()}
                  disabled={deleteMutation.isPending}
                  className="px-3 py-1.5 text-sm bg-destructive text-destructive-foreground rounded hover:bg-destructive/90 disabled:opacity-50"
                >
                  {deleteMutation.isPending ? 'Deleting...' : 'Yes, Delete'}
                </button>
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="px-3 py-1.5 text-sm bg-muted text-muted-foreground rounded hover:bg-accent"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10 rounded"
              >
                Delete
              </button>
            )}
          </div>
          <div className="flex gap-2">
            {isEditing ? (
              <>
                <button
                  onClick={() => setIsEditing(false)}
                  className="px-4 py-2 text-sm bg-muted text-muted-foreground rounded hover:bg-accent"
                >
                  Cancel
                </button>
                <button
                  onClick={() => updateMutation.mutate()}
                  disabled={updateMutation.isPending}
                  className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50"
                >
                  {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={onClose}
                  className="px-4 py-2 text-sm bg-muted text-muted-foreground rounded hover:bg-accent"
                >
                  Close
                </button>
                <button
                  onClick={() => setIsEditing(true)}
                  className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90"
                >
                  Edit
                </button>
              </>
            )}
          </div>
        </div>

        {/* Error Display */}
        {(updateMutation.isError || deleteMutation.isError) && (
          <div className="px-4 pb-4">
            <div className="text-sm text-red-600 dark:text-red-400 bg-red-500/10 rounded p-2">
              {deleteMutation.isError ? 'Failed to delete pattern.' : 'Failed to save changes.'} Please try again.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default PatternDetailModal;
