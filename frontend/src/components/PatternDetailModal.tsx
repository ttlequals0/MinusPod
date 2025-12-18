import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { AdPattern, updatePattern } from '../api/patterns';

interface PatternDetailModalProps {
  pattern: AdPattern;
  onClose: () => void;
  onSave: () => void;
}

function PatternDetailModal({ pattern, onClose, onSave }: PatternDetailModalProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedPattern, setEditedPattern] = useState({
    text_template: pattern.text_template || '',
    sponsor: pattern.sponsor || '',
    is_active: pattern.is_active,
    disabled_reason: pattern.disabled_reason || '',
  });

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
      return <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">Podcast: {pattern.podcast_id}</span>;
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
                <input
                  type="text"
                  value={editedPattern.sponsor}
                  onChange={(e) => setEditedPattern(prev => ({ ...prev, sponsor: e.target.value }))}
                  className="w-full px-3 py-2 bg-secondary border border-border rounded text-sm"
                />
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
              {pattern.created_from_episode_id && (
                <div>
                  <span className="text-muted-foreground">Created from:</span>
                  <span className="ml-2 text-foreground font-mono text-xs">{pattern.created_from_episode_id}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-border">
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

        {/* Error Display */}
        {updateMutation.isError && (
          <div className="px-4 pb-4">
            <div className="text-sm text-red-600 dark:text-red-400 bg-red-500/10 rounded p-2">
              Failed to save changes. Please try again.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default PatternDetailModal;
