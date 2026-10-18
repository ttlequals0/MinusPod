# UI behavior and review contract

Reviewed: 2026-10-16. Applies to desktop, phone, and tablet interfaces.

The HTML guide contains visual examples. Production components own behavior. Change a shared component when a rule applies across screens; do not patch each caller with a different recipe.

## Sources and checks

| Contract | Source | Required check |
| --- | --- | --- |
| Theme values | `frontend/src/index.css`, `frontend/src/themes/themes.ts` | `npm run design:sync`; `npm run design:check`; theme contrast tests |
| Page headers and summary cards | `pageStyles.ts`, Stats `StatCard` | Compare title, action placement, label/value alignment at every supported width |
| Buttons and fields | `buttonStyles.ts`, `fieldStyles.ts`, `rowActionStyles.ts`, global coarse-pointer CSS | Measure the clickable element in a browser |
| Dialogs | `Modal.tsx`, `useDraftClose.tsx` | Name, Tab cycle, Escape ownership, focus restoration, discard flow |
| Page tabs | `PageTabs.tsx` | Arrows, Home/End, selected state, panel relationship |
| Async failures | `QueryError.tsx` and query consumers | Failed requests cannot render an empty-success claim |
| Drafts and updates | `useUnsavedChanges.ts`, `App.tsx`, `main.tsx` | Navigation, unload, pull-to-refresh, explicit update application |
| Browser contracts | `frontend/e2e/ui-contract.spec.ts` | `npm run test:browser` |

Run these commands from `frontend`. The guide's theme catalog and default tokens are generated from application source. CI fails when the generated values differ. Static HTML examples are illustrations, not substitutes for exercising the React components.

## Responsive layout and targets

- Use at least 44 x 44 CSS px for standalone controls on coarse pointers, including tablets. An icon may be smaller. Padding outside the clickable element does not enlarge its target. On coarse pointers the global CSS makes the 44px height a hard floor. The 44px width is a base-layer default that any `min-w-*` utility overrides, so a flex row can give a button its own wider floor.
- Inline text links and range-slider tracks are exceptions to the square target rule. Checkbox and radio measurements use their associated clickable label, not an intentionally hidden native input.
- Preserve action and sorting access when tables become cards. Processing History and episode costs use cards below 1280 CSS px. Downloads uses cards below 768 CSS px and progressively reveals columns.
- Choose table breakpoints from populated content. Check long titles, large counts, IPv4 and IPv6 values, and error messages. Routine lists must fit their containers. Detailed comparison tables may scroll within a named, keyboard-focusable region.
- Keep Since and Until together in a two-column group at 320 CSS px. Each field has a label and reciprocal date limit. Date-only filters must retain their date-only semantics.
- Navigation collapses below 1280 CSS px. Keep the active route marked with `aria-current="page"` in both navigation layouts.
- Verify 320, 390, 768, 1024, 1280, and 1440 CSS px for changed layouts. Include a short viewport when changing dialogs or fixed bars.

## Data and feedback states

| State | Required behavior |
| --- | --- |
| Initial loading | Keep the page heading and context. Show a bounded skeleton or loading status. |
| Successful empty response | Explain what is empty and offer an appropriate next action. |
| Populated response | Keep filters, sorting, counts, and displayed results consistent. |
| Failure without data | State that data is unavailable and offer Retry. Do not claim no results or no active jobs. |
| Failure with cached data | Either label the data as stale or hide it with an error. Do not present it as current. |
| Mutation pending | Name the operation; prevent duplicate submissions. |
| Mutation failed | Preserve the draft and show an actionable error near the operation. |
| Partial success | Report completed and failed items separately; retry only the failed work. |

Associate visible labels with control IDs. Link help and validation with `aria-describedby`; expose invalid fields with `aria-invalid`. `NumberInput`, `DraftNumberInput`, and `TriStateSelect` forward `ariaDescribedBy`. An alert elsewhere in a form does not replace a field association.

Use a polite status announcement for successful saves and copies. Use an alert for errors requiring attention. Export and clipboard failures need visible feedback. A failure reason cannot exist only in a `title` tooltip; use readable text or a disclosure.

## Dialogs and keyboard

`Modal` requires `ariaLabel` or `ariaLabelledBy`. Prefer a visible heading. Custom waveform editors use the same focus and Escape hooks.

- Move focus inside on open; contain forward and reverse Tab, including any owned portaled controls.
- Make background content inert and lock background scrolling. Restore both when the dialog closes.
- Only the top dialog owns Escape. A popover inside it closes first. Return focus to the trigger if it still exists. Capture activation as well as focused elements: WebKit may leave a clicked button unfocused.
- Place dialogs above status and save bars; place their popovers above the dialog. Keep the panel inside the dynamic viewport and safe-area insets.
- Do not close data-entry dialogs on backdrop clicks. Dirty editors use `useDraftClose`; successful saves close without a discard prompt.
- Use Ctrl/Cmd+K for global quick search. Ordinary printable keys remain available to native controls, browser search, and assistive technology.
- Editor shortcuts run only when focus belongs to the editor. Ignore editable content, buttons, selects, sliders, composition, repeated keys, modifiers, and already-handled events.
- Page tabs use one Tab stop, Left/Right, Home/End, `aria-selected`, and a named `tabpanel`. Navigation links and filter buttons retain their own semantics.

## Saves, resets, and destructive actions

Each section states when its values are committed:

| Save policy | Presentation and protection |
| --- | --- |
| Explicit page save | Show Save Changes while dirty; retain values after failed saves. |
| Explicit section or field save | Put Save or Apply beside the owned values; register the draft independently. |
| Save on blur | Explain when the field saves; keep failed values dirty and retryable. |
| Immediate setting | State that changes apply immediately and report save failure. |
| Local appearance | Apply immediately; expose the selected mode programmatically. |

Keep independently saved drafts mounted when sections collapse. A query refresh must update clean values without replacing edited values. Clear a draft only after its save succeeds, and prevent edits while a save will replace the form. Compare values with their saved baseline so reverting an edit removes the guard.

Register unsaved state with `useUnsavedChanges` so router navigation, unload, pull-to-refresh, and app updates respect it. The global Save Changes button does not save independent sections. New independently saved sections must register their own drafts. `useDraftClose` handles explicit discard decisions in editors.

A service-worker update waits for the user's Reload to update action. Disable that action while registered drafts exist. A hidden tab is not proof that its work is saved.

Feed deletion, resets, and replace-mode imports use `ConfirmModal`. Name the object and consequence, provide Cancel, disable duplicate submissions, and retain errors for retry. Never require a second click within a timer. Do not translate a generic confirmation into a destructive default action.

## Contrast, hierarchy, layers, and motion

- Normal text requires at least 4.5:1 contrast against its rendered surface. Test ordinary text, muted text, status text, links, solid controls, hover states, and tinted badges across every theme and mode.
- Use the appropriate paired foreground token. Being token-based does not prove sufficient contrast. Disabled controls and decorative marks require separate interpretation; do not dilute active text with opacity.
- Use one visible page `h1` at 24 CSS px, section headings at 18 CSS px, and consistent summary-card labels/values. Keep numeric column headings aligned with their values.
- Use `pageHeader`, `pageTitle`, and `pageDescription`: 24 CSS px titles, 16 CSS px header gaps, 24 CSS px bottom spacing, and actions below the title on phones.
- Counter cards place the value above the label, left aligned. Metric cards place the label above the value and may add details. Both use 20 CSS px bold tabular values, 12 CSS px labels, and 16 CSS px outer padding. Metrics nested within a larger card may share its surface.
- Summary cards in the same row align their values and label starts, including clickable cards. Use an explicit shared layout; native button centering must not shift content when a neighboring label wraps.
- Layer order: sticky navigation 30, ordinary popovers and save bars 40, status bar 50, dialogs 60, dialog-owned popovers 70. Nested dialogs follow DOM order within the dialog layer.
- The status bar reports its measured occupied height to the root shell, before the update notice and page content. Sticky navigation and the mobile menu use the same inset. Do not reserve an unconditional gap or assume that safe-area padding is zero.
- Respect `prefers-reduced-motion` for spinners, copied-state animation, transitions, and smooth scrolling. Keep loading and status information understandable without animation.

## Review evidence

A UI PR includes affected routes, widths, input modes, and before/after images from the real components. State whether it used actual records, failed requests, or proposals. Never present an edited screenshot as an implemented screen.

CI runs type checking, lint, unit tests, guide parity, and browser contracts. The browser suite runs Chromium and WebKit at 320, 390, 768, and 1440 CSS px. It uses an unavailable backend for failure flows and checks accessible form names with axe. Set `MINUSPOD_POPULATED_URL` to a disposable instance URL ending in `/ui` to include the populated editor checks; CI skips those checks. Populated layouts require a local dataset and must not publish backup databases, credentials, subscriber addresses, or private configuration.

Record exceptions with the affected control, reason, owner, and follow-up issue. Browser emulation does not replace physical iOS checks for keyboards, safe areas, VoiceOver, or installed PWA updates.
