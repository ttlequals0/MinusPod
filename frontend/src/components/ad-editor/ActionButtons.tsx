import { X, Check, RotateCcw, Save } from 'lucide-react';
import { SaveStatus } from './types';

interface ActionButtonsProps {
  variant: 'desktop' | 'mobile-mini' | 'mobile-expanded';
  saveStatus: SaveStatus;
  onSave: () => void;
  onConfirm: () => void;
  onReject: () => void;
  onReset: () => void;
  getSaveButtonText: () => string;
  getConfirmButtonText: () => string;
  getRejectButtonText: () => string;
}

export function ActionButtons({
  variant,
  saveStatus,
  onSave,
  onConfirm,
  onReject,
  onReset,
  getSaveButtonText,
  getConfirmButtonText,
  getRejectButtonText,
}: ActionButtonsProps) {
  if (variant === 'desktop') {
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-3 bg-muted/30">
        <div className="flex items-center gap-3">
          <button
            onClick={onReset}
            disabled={saveStatus === 'saving'}
            className="px-4 py-2 text-sm font-medium rounded-lg border border-border bg-background hover:bg-accent disabled:opacity-50 transition-colors"
          >
            Reset
          </button>
          <button
            onClick={onConfirm}
            disabled={saveStatus === 'saving'}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
              saveStatus === 'saving'
                ? 'bg-green-600/50 cursor-wait'
                : saveStatus === 'success'
                ? 'bg-green-600'
                : 'bg-green-600 hover:bg-green-700'
            } text-white`}
          >
            {getConfirmButtonText()}
          </button>
          <button
            onClick={onSave}
            disabled={saveStatus === 'saving'}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
              saveStatus === 'saving'
                ? 'bg-primary/50 cursor-wait'
                : 'bg-primary hover:bg-primary/90'
            } text-primary-foreground`}
          >
            {getSaveButtonText()}
          </button>
        </div>
        <button
          onClick={onReject}
          disabled={saveStatus === 'saving'}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
            saveStatus === 'saving' ? 'bg-destructive/50 cursor-wait' : 'bg-destructive hover:bg-destructive/90 shadow-sm'
          } text-destructive-foreground`}
        >
          {getRejectButtonText()}
        </button>
      </div>
    );
  }

  if (variant === 'mobile-mini') {
    return (
      <div className="flex items-center gap-1.5 pt-0.5">
        <button
          onClick={onReject}
          disabled={saveStatus === 'saving'}
          className={`flex-1 py-2.5 rounded-lg touch-manipulation active:scale-95 transition-all flex items-center justify-center gap-1 text-xs font-medium disabled:opacity-50 ${
            saveStatus === 'saving' ? 'bg-destructive/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600 text-white' : saveStatus === 'error' ? 'bg-red-600 text-white' : 'bg-destructive/10 text-destructive active:bg-destructive/20'
          }`}
          title="Not an Ad"
        >
          <X className="w-3.5 h-3.5" />
          Not Ad
        </button>
        <button
          onClick={onReset}
          disabled={saveStatus === 'saving'}
          className="flex-1 py-2.5 rounded-lg bg-muted touch-manipulation active:scale-95 active:bg-accent transition-all flex items-center justify-center gap-1 text-xs font-medium disabled:opacity-50"
          title="Reset"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset
        </button>
        <button
          onClick={onConfirm}
          disabled={saveStatus === 'saving'}
          className={`flex-1 py-2.5 rounded-lg touch-manipulation active:scale-95 transition-all flex items-center justify-center gap-1 text-xs font-medium disabled:opacity-50 ${
            saveStatus === 'saving' ? 'bg-green-600/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600' : saveStatus === 'error' ? 'bg-red-600' : 'bg-green-600 text-white active:bg-green-700'
          }`}
          title="Confirm"
        >
          <Check className="w-3.5 h-3.5" />
          Confirm
        </button>
        <button
          onClick={onSave}
          disabled={saveStatus === 'saving'}
          className={`flex-1 py-2.5 rounded-lg touch-manipulation active:scale-95 transition-all flex items-center justify-center gap-1 text-xs font-medium disabled:opacity-50 ${
            saveStatus === 'saving' ? 'bg-primary/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600 text-white' : saveStatus === 'error' ? 'bg-red-600 text-white' : 'bg-primary text-primary-foreground active:bg-primary/90'
          }`}
          title="Save Adjusted"
        >
          <Save className="w-3.5 h-3.5" />
          Save
        </button>
      </div>
    );
  }

  // mobile-expanded
  return (
    <div className="flex items-center justify-center gap-2">
      <button onClick={onReject} disabled={saveStatus === 'saving'} className="flex-1 px-2 py-3 rounded-lg bg-destructive/10 text-destructive text-sm font-medium touch-manipulation active:scale-95 transition-all disabled:opacity-50">Not Ad</button>
      <button onClick={onReset} disabled={saveStatus === 'saving'} className="flex-1 px-2 py-3 rounded-lg bg-muted text-sm font-medium touch-manipulation active:scale-95 transition-all disabled:opacity-50">Reset</button>
      <button onClick={onConfirm} disabled={saveStatus === 'saving'} className="flex-1 px-2 py-3 rounded-lg bg-green-600 text-white text-sm font-medium touch-manipulation active:scale-95 transition-all disabled:opacity-50">Confirm</button>
      <button onClick={onSave} disabled={saveStatus === 'saving'} className="flex-1 px-2 py-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium touch-manipulation active:scale-95 transition-all disabled:opacity-50">Save</button>
    </div>
  );
}
