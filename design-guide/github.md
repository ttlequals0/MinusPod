repo: ttlequals0/MinusPod
branch: main

## Last review

date: 2026-10-16
baseline: 0864a235d2e212fc7c4833db99f91870a5ca576c

- [UI behavior and review contract](UI-CONTRACT.md) defines responsive, keyboard, data-state, save, and review requirements.
- [Visual reference](MinusPod%20Design%20System.dc.html) uses generated application theme values. Run `npm run design:sync` from `frontend`; CI runs `npm run design:check`.

## Source map

| Reference | Owning source |
| --- | --- |
| Tokens and themes | `frontend/src/index.css`, `frontend/src/themes/themes.ts` |
| Control recipes | `frontend/src/components/buttonStyles.ts`, `fieldStyles.ts`, `rowActionStyles.ts` |
| Dialogs and overlays | `Modal.tsx`, `Popover.tsx`, `GlobalStatusBar.tsx`, `Layout.tsx` |
| Tabs, filters, feedback | `PageTabs.tsx`, `DateField.tsx`, `SortControls.tsx`, `QueryError.tsx` |
| Draft protection | `frontend/src/hooks/useUnsavedChanges.ts`, `useDraftClose.tsx`, `frontend/src/App.tsx` |
| Browser checks | `frontend/e2e/ui-contract.spec.ts`, `frontend/playwright.config.ts` |
