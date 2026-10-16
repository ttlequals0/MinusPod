# MinusPod UI and UX audit

Date: 2026-10-16
Repository version: 2.100.15
Source commit: `0864a235d2e212fc7c4833db99f91870a5ca576c`

The [documentation gap review](DOCUMENTATION-GAPS.md) covers installation, Parakeet sizing, configuration examples, and deployment guidance.

## Implementation update

These changes fix shared failure paths and update the design guide. [Implemented before/after gallery](implemented-gallery.html) contains screenshots of the real changed frontend. The [earlier proposal gallery](gallery.html) remains a historical design reference; its after images are browser-only proposals.

The populated pass uses the supplied `minuspod-backup-20261016-203053.db`: 34 podcasts, 16,410 episodes, 4,267 processing-history records, 45 download-history records, 1,219 patterns, and 629 sponsors. Counts and the original file hash are recorded in [backup evidence](backup-evidence.json). The app runs against a disposable copy with background work, external connections, and mutation requests disabled.

| Audit item | Implementation | Verification or remaining limit |
| --- | --- | --- |
| UI-01 | Retry states for Settings, search, queue, history, and sponsors; pending search is distinct from no matches | Browser failure flows; populated route sweep |
| UI-02 | Required dialog names, shared focus containment, inert background, nested Escape, scroll lock, restored focus; cue dialogs use the same behavior | Nested import browser test; existing editor tests; real media unavailable |
| UI-03 | Ctrl/Cmd+K global search; editor shortcuts respect controls, composition, modifiers, repeat, and focus ownership | Browser shortcut checks and editor unit tests |
| UI-04 | Updated theme tokens; tests cover text, filled controls, hover, status colors, and existing tint combinations | Original 400-pair matrix: 122 failures before, zero after; expanded theme suite passes |
| UI-05 | Coarse-pointer minimums apply across viewport widths; row-action recipe uses 44 CSS px targets | Phone and tablet browser checks; visual review |
| UI-06 | Shared PageTabs; active navigation, pagination, and appearance selection semantics | Arrow/Home/End browser test and panel association checks |
| UI-07 | Search label and wrapping filters; maintenance control moved below results; sponsor/pattern destinations carry the selected name | Real populated search at 390, 768, and 1440 CSS px |
| UI-08 | Explicit feed-delete, reset, and replace-import confirmations; no confirmation timer | Updated mutation unit tests; nested import browser test |
| UI-09 | Registered page/section drafts protect navigation, unload, refresh, and update application; sponsor, pattern, ad, and cue editors confirm discard | Draft lifecycle tests; populated sponsor discard check; physical-device and installed-PWA checks remain |
| UI-10 | Date range is a nested paired group | Browser bounds at 320, 390, 768, and 1440 CSS px |
| UI-11 | Mobile feed list exposes Force Refresh; History/Downloads expose sorting; History and episode costs use cards below 1280 CSS px; Downloads cell padding fits real content | Populated History at 768 CSS px and Downloads at 1440 CSS px |
| UI-12 | Import labels/status, pattern textarea recipe, copy/export failure feedback, visible feed-refresh details | Browser import checks and existing component tests; broader field associations remain a follow-up |
| UI-13 | Measured status-bar inset, named layer order, safe-area padding and dynamic dialog height | Rendered shell and modal checks; notched physical devices remain unverified |
| UI-14 | Shared reduced-motion CSS | Source/build verification; manual assistive-technology review remains |
| UI-15 | Generated theme catalog and tokens; corrected target and breakpoint guidance; new behavior contract | Guide parity check |
| UI-16 | Playwright browser contracts in CI; expanded contrast tests; UI evidence in PR template | 28 local browser checks (24 backend-free, 4 populated); 1,826 unit tests; lint and production build |
| UI-17 | History summary-card typography and stable loading headings | Populated History comparison; wider page-spacing redesign remains a proposal |

### Populated findings

The backup exposed problems that the initial failed-request screens could not establish:

| Surface | Before | After |
| --- | --- | --- |
| Search, 390 CSS px | Filter region: 731 CSS px content in 358 CSS px available | Filters wrap; no horizontal scrolling region |
| Search, 768 CSS px | Filter region: 731 CSS px content in 720 CSS px available | Filters fit |
| Processing History, 768 CSS px | Table: 1,123 CSS px content in 718 CSS px available | Cards with visible sorting controls |
| Downloads, 1440 CSS px | Table: 1,246 CSS px content in 1,214 CSS px available | Reduced cell padding; table fits |
| Episode cost table, 768 CSS px | Table: 1,076 CSS px content in 668 CSS px available | Cards at this width |

The populated sweep covers 14 route variants at 390, 768, and 1440 CSS px, for 42 captured states per revision. Neither revision has document-level overflow or uncaught page errors in those captures. Detailed episode run tables still scroll horizontally; their regions now have keyboard access and descriptive labels. Stats section navigation is intentionally scrollable. [Populated measurements](populated-evidence.json) record these distinctions.

### Implemented image examples

| Search at 390 CSS px: before | Implemented after |
| --- | --- |
| ![Populated search before](images/data-search-390-before.png) | ![Populated search after](images/data-search-390-after.png) |

| History at 768 CSS px: before | Implemented after |
| --- | --- |
| ![Populated History before](images/data-history-768-before.png) | ![Populated History after](images/data-history-768-after.png) |

### Remaining work and release checks

- Complete label/help/error associations in the remaining settings and Add Feed fields. The concrete field targets in UI-12 remain applicable beyond the import and pattern-editor fixes.
- Register drafts in any independently saved settings section that still owns local state outside the page's Save Changes model. The contract requires this; the new guard cannot infer ownership from arbitrary inputs.
- Verify waveform playback, cue scanning, successful saves, recuts, and active-job cancellation with retained media and a writable test instance. The supplied database does not include audio or artwork files.
- Verify VoiceOver, physical iOS keyboards/safe areas, WebKit date rendering, browser zoom, and production PWA upgrade behavior. These are release checks, not claims covered by Chromium screenshots.
- Decide whether to extend the proposed page-spacing and summary-card normalization beyond History. Existing screenshots distinguish the implementation from that broader proposal.

The detailed findings below describe the original source revision. Line-specific source links are pinned to that commit so the evidence remains readable after the changes.

## Findings at the baseline

The design guide needs changes, but missing documentation explains only part of the drift. Several current defects violate rules already in the guide. Some shared components make compliance optional, and CI does not check rendered layouts or complete interaction flows.

- Settings repeatedly returns to its loading skeleton when its API is unavailable. Queue reports that nothing is processing while its requests fail. Quick search reports no matches after a failed request.
- The Import patterns dialog has no accessible name. Its file input and Mode selector have no associated labels.
- Search filters measure 36 CSS px high on a phone. The guide requires a 44 CSS px clickable target.
- Downloads separates its date fields at a 360 CSS px viewport, despite the guide requiring a paired range at that width.
- Default Slate light primary-button text has a calculated contrast ratio of 3.215:1. Normal text requires 4.5:1.
- The guide contains 11 theme definitions; the application contains 20. It also describes an outdated navigation breakpoint.

The frontend test baseline passes: 147 test files and 1,824 tests.

## Initial scope and evidence limits

This is a repository-wide audit of page structure, shared controls, responsive rules, state handling, and design governance. Initial runtime inspection covered failed-request states; the implementation pass also covers the supplied populated backup as described above.

| Area | Coverage |
| --- | --- |
| Source | Route entry points, shared styling and interaction primitives, themes, representative settings sections, feed and episode flows, review and cue dialogs, changelog, guide, CI |
| Browser | Local Vite frontend in Chrome 155.0.8059.80; real components and actual failed API requests |
| Width sweep | 320, 360, 390, 640, 768, 1024, 1280, and 1440 CSS px; 11 route variants |
| Comparison images | Phone views at 390 x 844 CSS px; desktop views at 1440 x 1000 CSS px; additional 360 x 844 CSS px date and dark-dialog views |
| Input modes | Width sweep requests touch emulation; phone control-size comparisons verify coarse-pointer behavior; desktop control comparisons use mouse input. Settings captures test the failure presentation only. |
| Themes | Slate light screenshots, Slate dark dialog screenshots, calculated token-pair checks across all 20 themes in both modes |
| Tests | Existing frontend Vitest suite; targeted browser measurements and keyboard checks; token contrast calculations |
| Unverified | Successful server mutations, populated lists and charts, audio playback and waveform editing with real media, live processing, authentication success, native iOS keyboard, WebKit, VoiceOver, installed PWA updates |

The initial width sweep found no document-level horizontal overflow in the captured states. It did find horizontally clipped filter/tab contents. This does not establish that populated tables or long episode titles fit.

Evidence labels:

- **Observed:** reproduced in the browser against the current frontend.
- **Source:** a concrete implementation issue visible in the linked code, without a complete runtime reproduction.
- **Calculated:** derived from the repository's actual theme values.
- **Proposal:** a recommended design decision, rather than a claim that the current behavior is broken.

Raw evidence: [viewport sweep](runtime-evidence.json), [comparison measurements](preview-evidence.json), [keyboard and date checks](interaction-evidence.json), [settings failure check](settings-proof.json), and [contrast calculations](contrast-evidence.json).

## Why the drift keeps returning

The changelog shows repeated fixes to the same families of controls. These entries establish recurrence; they do not mean every old bug remains present.

| Release | Recorded change | Current implication |
| --- | --- | --- |
| [2.87.0, 2026-08-12](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L2591) | Shared field recipes, dialog focus handling, keyboard menus, badge and select consolidation | Reuse exists, but custom dialogs and fields still bypass it. |
| [2.94.0, 2026-08-30](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L1914) | Matching action heights and slimmer switches; queue layout repairs | Row-specific sizing has continued alongside the later global touch-target rule. |
| [2.94.1, 2026-08-30](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L1793) | Switch shrink prevention, narrow pagination, header-action sizing | Width and height constraints require rendered checks, not just class-name assertions. |
| [2.95.3, 2026-09-05](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L1371) | Coarse-pointer fields use 16 CSS px text to prevent mobile focus zoom | This centralized fix is the right pattern. Touch-target sizing still depends mainly on viewport width. |
| [2.97.4, 2026-09-15](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L938) | Shared 44 CSS px hit target, guide additions, component contracts | `touchTarget` remains opt-in; CopyButton and Pagination still omit it. |
| [2.98.0, 2026-10-06](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L338) | More 44 CSS px fixes; OPML dialog naming | Pattern import and other dialogs still lack names. A repaired call site did not establish a universal contract. |
| [2.99.0, 2026-10-09](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L225) | Aligned filters and paired date fields | Downloads still places the two date fields directly in the outer responsive grid. |
| [2.100.13, 2026-10-14](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/CHANGELOG.md#L37) | Unified detection-card layout and compact mobile feed filters | Recent improvements need regression coverage across neighboring screens and shared recipes. |

The structural causes are:

1. **Contracts are split between prose and call sites.** Button recipes define color, while callers supply size, focus, spacing, and disabled treatment.
2. **The guide contains conflicting instructions.** A developer can follow its mobile button example and still violate its final rules.
3. **The guide duplicates runtime definitions.** Its standalone theme and component examples can diverge from the components they describe.
4. **Feature dialogs bypass shared behavior.** Sharing colors does not share focus management, labeling, stacking, or dismissal.
5. **Tests stop before browser behavior.** Component tests pass while targets remain small, labels remain disconnected, and failure screens misreport state.

## Prioritized repair list

P1 means misleading state, accidental action, lost work, or an accessibility barrier. P2 means recurring interaction or consistency defects. P3 covers polish and lower-risk consistency work.

| ID | Priority | Fix | Evidence |
| --- | --- | --- | --- |
| UI-01 | P1 | Separate loading, failed, stale, and empty data states | Observed + source |
| UI-02 | P1 | Complete and enforce dialog accessibility | Observed + source |
| UI-03 | P1 | Scope keyboard shortcuts and protect form controls | Observed + source |
| UI-04 | P1 | Correct normal-text contrast and extend theme tests | Calculated |
| UI-05 | P2 | Enforce target sizes through shared controls | Observed + source |
| UI-06 | P2 | Implement tab, navigation, and selection semantics consistently | Observed + source |
| UI-07 | P2 | Repair search hierarchy and result destinations | Observed + source + proposal |
| UI-08 | P1 | Replace timed destructive confirmation with explicit decisions | Source + proposal |
| UI-09 | P1 | Make save boundaries and unsaved-work protection explicit | Source |
| UI-10 | P2 | Keep date ranges paired below 375 CSS px | Observed |
| UI-11 | P2 | Preserve mobile action and table parity | Source |
| UI-12 | P2 | Finish field, validation, and feedback consistency | Observed + source |
| UI-13 | P2 | Define shell spacing, overlay layers, and safe-area behavior | Observed + source + proposal |
| UI-14 | P3 | Apply reduced-motion behavior consistently | Source |
| UI-15 | P2 | Remove guide contradictions and duplicate definitions | Source |
| UI-16 | P2 | Add browser and design-contract gates to CI | Source |
| UI-17 | P3 | Normalize page and summary-card hierarchy | Source + proposal |

## Detailed findings

### UI-01: Failed requests can look like empty data or remain loading

**Observed:** Queue displays "No episodes are processing right now" above failed queue requests. Quick search displays "No shows, episodes or transcripts match" after the search request fails. Settings repeatedly shows a skeleton during sustained failed reads.

**Source:** [QueuePage](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/QueuePage.tsx#L109) falls through to empty text when neither status source has data. [useUnifiedSearch](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/hooks/useUnifiedSearch.ts#L28) discards loading and error state; [SearchResults](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/SearchResults.tsx#L84) treats an empty array as no matches. [Settings](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/Settings.tsx#L405) reads `isLoading` but does not render a terminal settings-query error branch before its form.

Several settings children also subscribe to `['settings']`, including [OutboundRequestsSection](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/settings/OutboundRequestsSection.tsx#L33). Mounting those children after a failed parent query can restart fetching. This is a source-supported explanation for the repeated loading behavior; an isolated regression test should pin the exact retry cycle.

Dashboard, Sponsors, and History show failure text but offer no local retry. Processing History also loses its page heading in that branch. Stats already has retryable error panels that provide a useful existing pattern.

**Fix:** Give every query region distinct initial loading, success-empty, success-populated, failed, and stale-refresh states. Only show an empty-state claim after a successful response. Keep the title and navigation mounted. Expose Retry near the failed region. Settings must require successfully loaded values before rendering editable server-backed controls.

**Acceptance:** A sustained HTTP 502 produces a stable named error region and a usable Retry action. Failed search never says no matches. Failed queue reads never assert that processing is idle. Cached data is visibly marked stale if retained.

### UI-02: Dialog naming and focus behavior are incomplete

**Observed:** Import patterns has `role="dialog"` and `aria-modal="true"`, but no accessible name. Its file input and Mode select also have no associated labels. Browser evidence records each missing association.

**Source:** [Modal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/Modal.tsx#L61) makes its name optional. [ConfirmModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/Modal.tsx#L118) renders a heading without linking it. [PatternImportDialog](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/PatternImportDialog.tsx#L62), [PatternDetailModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/PatternDetailModal.tsx#L113), and FeedDetail's confirmation dialogs omit `ariaLabelledBy`.

The shared Modal already traps Tab and restores focus. Preserve that behavior. [AdReviewModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/AdReviewModal.tsx#L951), [CueMarkModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/CueMarkModal.tsx#L584), and [CueCrossEpisodeScanModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/CueCrossEpisodeScanModal.tsx#L171) implement their own wrappers without the shared trap. CueMark moves initial focus, which does not keep subsequent Tab navigation inside the dialog.

**Fix:** Require a name through a title prop or a required labeling choice. Move the custom wrappers onto the shared dialog behavior while preserving their editor content. Define background inertness, scroll locking, focus restoration, nested-dialog ownership, and maximum usable height. Give create mode an "Add new ad" name instead of always "Review ad".

**Acceptance:** Each dialog and field has a programmatic name. Tab and Shift+Tab stay inside the top dialog; Escape closes only that dialog. Closing returns focus to its trigger. Check short landscape viewports and keyboard-reduced space. Follow the [WAI modal-dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).

### UI-03: Shortcuts intercept ordinary control interaction

**Observed:** Pressing a plain `x` while the Import button has focus opens Quick search.

**Source:** [useQuickSearchHotkey](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/QuickSearch.tsx#L18) captures printable characters globally. It has no user-facing disable/remap setting. [AdReviewModal's keyboard handler](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/AdReviewModal.tsx#L903) exempts INPUT and TEXTAREA, but not SELECT, contenteditable fields, or buttons. Its C, R, and S handlers can submit a verdict or skip; Space overrides native button behavior. Modifier keys and repeated keydown events are not excluded.

**Fix:** Keep Ctrl/Cmd+K as the global search shortcut. Make single-character shortcuts opt-in or active only within the relevant focused widget. Exclude native editing controls and unrelated buttons, respect `defaultPrevented`, ignore key repeat for mutations, and give the top dialog sole ownership of editor shortcuts.

**Acceptance:** Typing in category selects never confirms, rejects, or skips an ad. Space activates the focused button. Ctrl/Cmd+C cannot trigger Confirm. Holding a key cannot submit repeated verdicts. Single-character shortcuts meet [WCAG's disable, remap, or focus-scoping requirement](https://www.w3.org/WAI/WCAG22/Understanding/character-key-shortcuts.html).

### UI-04: Token use does not guarantee sufficient contrast

The current [theme tests](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/themes/themes.test.tsx#L65) check badge tints and two solid chip pairings. They do not cover ordinary primary buttons, destructive buttons, links, or muted body text.

Calculated from [index.css](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/index.css#L5), using the WCAG sRGB luminance formula:

| Slate pairing | Mode | Contrast ratio | Normal-text result |
| --- | --- | --- | --- |
| Primary foreground on primary | Light | 3.215:1 | Below 4.5:1 |
| Primary text on page background | Light | 2.870:1 | Below 4.5:1 |
| Destructive foreground on destructive | Light | 4.205:1 | Below 4.5:1 |
| Muted foreground on page background | Light | 4.296:1 | Below 4.5:1 |
| Destructive foreground on destructive | Dark | 3.250:1 | Below 4.5:1 |
| Muted foreground on card | Dark | 3.691:1 | Below 4.5:1 |

The audit calculated 400 combinations: 20 themes x 2 modes x 10 text/surface pairings. Of these, 122 are below 4.5:1. This is token-contract coverage, not 122 separately observed UI defects. Null theme modes use the stylesheet defaults. Unrounded ratios determine pass/fail; the table rounds display values only.

**Fix:** Extend the existing theme tests to all permitted text/surface pairs, including active, hover, translucent, and nested states. Adjust tokens centrally. Separate a brand accent from readable link text if the same hue cannot satisfy both roles. Check non-text boundaries and focus indicators separately.

**Acceptance:** Permitted normal-text pairings reach 4.5:1; qualifying large text reaches 3:1. A theme must pass before it can be added. Disabled controls require separate treatment and are not counted as normal-text failures here. See [WCAG contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

The previews darken Slate light primary and muted text, and lighten Slate dark muted text. These are review candidates for the default theme, not approved replacements for every theme.

### UI-05: Shared controls still allow undersized touch targets

**Observed:** Search filter buttons are 36 CSS px high. Patterns Import/Export buttons are 32 CSS px high; its tabs are 38 CSS px high. The Add Feed advanced disclosure is 20 CSS px high and its browse button is 24 CSS px high.

**Source:** [CopyButton](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/CopyButton.tsx#L39) uses a 32 x 32 CSS px mobile box. [Pagination](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/Pagination.tsx#L24) has no touch-target floor. [FeedListItem](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/FeedListItem.tsx#L112) uses 32 CSS px delete controls. [rowActionStyles](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/rowActionStyles.ts#L8) specifies 40 CSS px mobile marker controls. [SegmentedToggle](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/SegmentedToggle.tsx#L48) defaults `mobileTouchTarget` to false.

[touchTarget](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/buttonStyles.ts#L17) removes its minimum at `sm`, so a landscape phone or tablet can get compact desktop targets despite using touch.

**Fix:** Make a 44 x 44 CSS px hit area the shared coarse-pointer default. Keep icons and switch tracks visually compact inside it. Centralize control-size variants alongside the existing recipes. Audit exceptions at the interactive element, including labels that legitimately enlarge checkbox hit areas.

**Acceptance:** Measure actual clickable bounds at narrow and landscape widths. Check shared controls and callers. The repo's 44 CSS px rule is a product standard; WCAG 2.2 AA has a 24 CSS px minimum with exceptions. Do not label every sub-44 CSS px control an AA violation. See [target size minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum).

### UI-06: Tabs look complete but lack their keyboard contract

**Observed:** ArrowRight does not move focus from Patterns to Detected Ads. All four tabs remain in the normal Tab sequence, and none has `aria-controls`.

**Source:** [PatternsPage](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/PatternsPage.tsx#L273) and [HistoryPage](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/HistoryPage.tsx#L413) add tab roles and selection state without roving focus or linked panels. [Layout's NavLink](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/Layout.tsx#L30) styles the active route but omits `aria-current`. Search filters do not expose their selected state. Appearance theme buttons use a visual checkmark without a selected-state attribute.

**Fix:** Share a page-tabs implementation with an explicit activation model and linked panels. Preserve the existing horizontal reveal behavior for long tab sets. Use `aria-current="page"` for navigation and `aria-pressed` or radio semantics for choices.

**Acceptance:** Tab enters a tab set once; arrow keys navigate it; Home/End work; the active panel is associated with its tab. Selection remains understandable without color. Follow the [WAI tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).

### UI-07: Search gives maintenance work too much prominence and has broken destinations

**Observed:** At 390 CSS px, Rebuild Index squeezes the page heading area and wraps onto two lines. Patterns and Sponsors filters sit beyond the initially visible horizontal strip.

**Source:** [Search](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/Search.tsx#L117) sends sponsor results to `/patterns`, despite the separate `/sponsors` route. Pattern results also open the general list without identifying the selected pattern. Its search field relies on placeholder text rather than a persistent label. Quick search's combobox similarly has no explicit accessible name.

**Fix:** Route sponsor results to Sponsors and give result destinations enough state to locate the chosen item. Add visible or programmatic search labels. Keep all six filters visible on phones, or use a labeled selector. Move Rebuild Index into secondary maintenance controls. Show rebuild success and errors; the mutation currently has no rendered outcome.

**Acceptance:** Each result opens its expected entity or a filtered destination containing it. All result types are discoverable at 320 CSS px. Rebuilding does not compete with the primary search task and always reports its outcome.

The wrapping filter layout and maintenance disclosure shown in the images are proposals. The wrong sponsor destination and missing feedback are implementation defects.

### UI-08: Destructive confirmation depends on timing and varies by screen

**Source:** [Dashboard](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/Dashboard.tsx#L133) and [FeedDetail](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/FeedDetail.tsx#L297) arm deletion for 3 seconds. Confirmation appears in a separate toast while the delete control keeps its ordinary label. [ConfirmResetButton](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/settings/ConfirmResetButton.tsx#L41) uses another timed second-click pattern. Sponsor deletion instead uses ConfirmModal. Pattern import offers "Replace: delete all patterns, then import" through its ordinary Import button.

**Fix:** Define confirmation by consequence. Feed deletion, replacing patterns, bulk deletion, and Reset All should show the affected object or scope and explicit Cancel/Confirm actions. Keep confirmation adjacent or modal, without a timer that requires hurried reading. Reserve immediate actions for operations with a reliable undo.

**Acceptance:** Accidental double activation cannot remove a feed. Consequences remain visible until a decision. Confirm names its action and scope, disables duplicate submissions, and preserves an actionable error on failure. Keep existing per-item eligibility rules.

### UI-09: Save boundaries and draft protection are unclear

**Source:** [Settings](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/Settings.tsx#L1781) has a global Save Changes bar, but many sections save immediately. [FeedSettingsPanel](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/feeds/FeedSettingsPanel.tsx#L269) mixes explicit Save, blur commits, and immediate mutations. The settings page tracks dirty state, but has no navigation or unload guard. The PWA update handler [reloads a hidden tab](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/main.tsx#L12), which does not establish that its drafts are saved. Pull-to-refresh [reloads the window](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/App.tsx#L165) without checking dirty state.

**Fix:** Classify sections as "Applies immediately" or "Save required" and display that policy near their controls. Preserve drafts across background refetches. Add an explicit dirty-state policy for navigation, refresh, modal close, and service-worker updates. Offer update application when drafts are safe, or persist and restore them.

**Acceptance:** Editing an unsaved setting, switching tabs, receiving an app update, and returning cannot silently discard the draft. A failed save keeps the value and error near its field. The global Save bar accurately represents the fields it owns. Verify with a real backend and production PWA build.

### UI-10: Downloads breaks the documented paired date range

**Observed:** At 360 CSS px, Since starts at y=447 CSS px and Until at y=531 CSS px. Each field is 328 CSS px wide. They are independent rows.

**Source:** [DownloadsTab](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/history/DownloadsTab.tsx#L139) places both DateFields directly in `filterGrid`. [filterGrid](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/fieldStyles.ts#L13) has one column below 375 CSS px. Stats already uses a nested two-column range.

**Fix:** Give the range its own two-column grid with `min-width:0` children, separate labels, and reciprocal limits. Reuse DateField. Specify date-only semantics and timezone near the range where relevant.

**Acceptance:** At 320, 360, and 390 CSS px, both fields share a row with complete values and at least 44 CSS px height. The 360 CSS px preview measures two 160 CSS px fields, separated by 8 CSS px. Filled native inputs still require WebKit validation.

### UI-11: Mobile layouts do not always preserve desktop actions

**Source:** [FeedListItem](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/FeedListItem.tsx#L72) shows a direct Refresh button on mobile, while its desktop menu includes Force refresh. [FeedCard](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/FeedCard.tsx#L93) retains that menu on mobile. Changing the dashboard view therefore changes which action is available.

[History](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/HistoryPage.tsx#L284) exposes sorting in desktop table headers without an equivalent mobile sort control. Downloads does the same. Stats model-usage cards already demonstrate mobile sorting. Populated History and Stats tables also need intermediate-width checks before their existing `overflow-x-auto` wrappers can be considered harmless.

**Fix:** Define an action and information inventory for each table/card pair. Preserve filter, sort, bulk selection, status detail, and row-menu capabilities when the layout changes. Specify each table's card breakpoint from its columns and content, rather than column count alone.

**Acceptance:** Switching grid/list or desktop/mobile does not remove an operation. Test long titles, IPv6 addresses, large amounts, and dense action rows at 640, 768, 1024, and 1280 CSS px using real retained data or explicitly approved fixtures. The populated pass above subsequently reproduced History, Downloads, and episode-cost table overflow.

### UI-12: Field recipes and actionable feedback still diverge

**Observed:** The Import dialog's visible Mode label is not associated with its select. The file input is unnamed.

**Source:** [PatternDetailModal](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/PatternDetailModal.tsx#L222) styles Text Template with `bg-secondary` and a small radius, contrary to the guide's textarea recipe. Its label has no `htmlFor`. [History](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/HistoryPage.tsx#L257) truncates failure text and relies on `title` for the full message. [FeedCard](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/FeedCard.tsx#L63) exposes refresh failure details through a tooltip. History export failures are only logged to the console. [CopyButton](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/CopyButton.tsx#L30) has no visible failure response, and [SavedBadge](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/settings/SavedBadge.tsx#L3) has no live status semantics.

Remaining examples: [Add Feed URL validation](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/pages/AddFeed.tsx#L633) changes the border and renders an error without `aria-invalid` or `aria-describedby`. [Sponsor edit fields](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/SponsorEditModal.tsx#L85) have visible labels that are not associated with their inputs. These need a follow-up field migration.

**Fix:** Finish migration to existing field recipes. Pair each control with its label, help, and validation message. Reuse an expandable failure-detail pattern with Copy details. Report export, copy, and save outcomes where the user acted; use a restrained live region for status changes.

**Acceptance:** Touch and keyboard users can read full errors. Every form control has an accessible name. Invalid fields expose `aria-invalid` and their error association. A failed export or clipboard operation never appears to have succeeded.

### UI-13: Shell spacing and overlay ownership need one contract

**Observed:** The shell reserves a blank 40 CSS px strip even when no status bar is rendered. [Layout](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/Layout.tsx#L112) always mounts `h-10`, while [GlobalStatusBar](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/GlobalStatusBar.tsx#L260) returns null without activity.

**Source:** Global status, modal backdrops, and the Settings save bar all use `z-50`. Their order can depend on DOM placement. The viewport declares `viewport-fit=cover`, but the frontend has no `env(safe-area-inset-*)` treatment for fixed controls. Modal heights also vary between `vh` recipes.

**Fix:** Decide whether the status area is permanent or conditional. If permanent, give it meaningful idle/unavailable content; if conditional, remove its spacer when hidden. Define named layer levels and measured top/bottom insets. Apply safe-area padding to fixed bars and use available viewport height for dialogs.

**Acceptance:** No unused band when the status strip is absent; no hidden focused control behind a fixed bar; modals remain above status and save controls. Validate notched-device portrait/landscape and virtual-keyboard behavior on a real device. The images show the conditional-space proposal.

### UI-14: Reduced motion is handled only in isolated controls

**Source:** Pin includes `motion-reduce` classes, but [LoadingSpinner](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/src/components/LoadingSpinner.tsx#L1), CopyButton's scale animation, skeleton pulses, collapsible height transitions, and smooth scrolling lack a shared reduced-motion policy.

**Fix:** Define reduced-motion behavior for each animation family. Remove nonessential scaling and smooth movement when requested; retain readable static loading/status feedback. Do not globally remove all state indication.

**Acceptance:** With reduced motion enabled, controls still communicate loading and success without scale effects or animated scrolling. Include this setting in browser checks.

### UI-15: The guide contradicts itself and omits behavioral contracts

| Guide item | Conflict or omission | Required correction |
| --- | --- | --- |
| [Mobile buttons](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/design-guide/MinusPod%20Design%20System.dc.html#L140) | Recommends 32 x 32 CSS px icons and a larger surrounding row. [Final rules](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/design-guide/MinusPod%20Design%20System.dc.html#L577) require 44 CSS px on the clickable element and reject wrapper spacing. | Replace the old example and distinguish visual size from hit area. |
| Row action examples | Documents 40 CSS px controls beside the 44 CSS px universal rule. | State one rule and any justified exceptions; update the source recipe. |
| Navigation | Says the menu collapses below `sm`. Layout uses `xl`, or 1280 CSS px with the current defaults. | Document the actual shell breakpoint and its reason. |
| Theme examples | Contains 11 copied themes while `themes.ts` contains 20. | Generate theme options/values from source or validate parity automatically. |
| Component examples | Standalone HTML repeats recipes instead of rendering production components. | Use live component examples for behavior; mark static illustrations as non-authoritative. |
| Dialogs | Describes appearance and shared Modal use, without an enforceable required name, focus, inertness, and nested-dialog checklist. | Add a complete modal contract to the primitive API and guide. |
| Data states | Skeletons and inline errors are shown, but failed, stale, empty, and unavailable data are not distinguished. | Add a required async-state matrix. |
| Saves and destructive actions | Multiple commit policies coexist; timed second-click confirmation and ConfirmModal are both permitted without scope rules. | Specify save ownership, dirty-state protection, and confirmation by consequence. |
| Touch and device behavior | Width-based minimums leave landscape touch devices uncovered. | Specify pointer capability, safe areas, zoom, virtual keyboard, and short-height behavior. |
| Maintenance | [github.md](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/design-guide/github.md#L5) still reports an August sync date despite later guide additions. | Record the actual reviewed commit, owner, and generated/reference sections. |

**Fix:** Make each guide rule point to its owning component or token and its validation. Document exceptions next to the rule, with scope and rationale. Remove contradictory old samples instead of appending another correction.

### UI-16: CI does not prevent the observed drift

**Source:** [CI](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/.github/workflows/ci.yml#L87) runs lint, Vitest, and a build. [Vitest](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/frontend/vitest.config.ts#L7) uses happy-dom. [The browser smoke script](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/smoke/local/05-ui-playwright.sh#L1) is explicitly a stub; it skips the browser assertions. The [PR template](https://github.com/ttlequals0/MinusPod/blob/0864a235d2e212fc7c4833db99f91870a5ca576c/.github/PULL_REQUEST_TEMPLATE.md#L7) asks for code checks without responsive or accessibility evidence.

**Fix:** Add browser checks to CI using the actual app and components. Extend existing unit tests for behavioral contracts rather than asserting more class strings. Add screenshot comparisons for critical states and viewport families. Keep baselines tied to the source revision and require review of intentional visual changes.

**Acceptance:** CI catches a missing dialog name, unpaired date range, undersized shared target, false-empty error state, and changed tab keyboard behavior. Every UI PR records relevant mobile/desktop states and the guide rule it affects. Use stored real data only with permission; do not silently invent test datasets.

### UI-17: Page hierarchy and summary cards still vary

**Source:** History's summary values use `text-2xl` and captions use `text-sm`; the guide's Counter variant specifies `text-xl` values and `text-xs` captions. Several numeric History SortHeaders retain default left alignment. Settings, Search, Add Feed, and list pages each construct their own header/action row.

**Fix:** Use the guide's named Counter and Metric variants consistently, or document a deliberate third variant. Standardize header spacing and the mobile placement of secondary actions. Keep page titles visible during loading and errors. Align numeric headers with numeric cells.

**Acceptance:** Equivalent cards share typography and spacing across pages; responsive headers preserve title prominence; error states retain orientation. Treat layout refinements as reviewable design choices, not automatic accessibility failures.

## Initial before and proposed after

**Before images are screenshots of the current application. After images are browser-only presentation prototypes of the same screens.** They predate the implementation and do not show its final behavior.

The prototypes use CSS and targeted DOM changes on the actual rendered UI. They preserve the existing product structure and do not invent podcast data. New Retry buttons illustrate the proposed action; they are not connected implementations. A screenshot cannot verify focus trapping, mutation safety, or persistence.

Open [the visual comparison gallery](gallery.html) for larger images. Each full-resolution PNG is also linked below.

### Add Feed: target sizes, contrast, and unused header space

Related: UI-04, UI-05, UI-13. Keep the current feed types and OPML workflow. Enlarge the disclosure and browse action, strengthen text contrast, and remove the unused status spacer.

| View | Current | Proposed |
| --- | --- | --- |
| Phone, 390 x 844 CSS px | ![Current Add Feed on phone](images/add-390-before.png) | ![Proposed Add Feed on phone](images/add-390-after.png) |
| Desktop, 1440 x 1000 CSS px | ![Current Add Feed on desktop](images/add-1440-before.png) | ![Proposed Add Feed on desktop](images/add-1440-after.png) |

### Search: visible filters and a clearer primary task

Related: UI-04 through UI-07, UI-13. Keep all result types visible, add a persistent field label, and put index maintenance below the search task. The phone filters measure 44 CSS px high in the proposal.

| View | Current | Proposed |
| --- | --- | --- |
| Phone, 390 x 844 CSS px | ![Current Search on phone](images/search-390-before.png) | ![Proposed Search on phone](images/search-390-after.png) |
| Desktop, 1440 x 1000 CSS px | ![Current Search on desktop](images/search-1440-before.png) | ![Proposed Search on desktop](images/search-1440-after.png) |

### Import patterns: labels and consistent dialog controls

Related: UI-02, UI-04, UI-05, UI-12. Associate the title and fields, add a visible file label, and use consistent action height and footer separation. The dark comparison isolates muted-text contrast and control height.

| View | Current | Proposed |
| --- | --- | --- |
| Phone, 390 x 844 CSS px | ![Current Import dialog on phone](images/import-390-before.png) | ![Proposed Import dialog on phone](images/import-390-after.png) |
| Desktop, 1440 x 1000 CSS px | ![Current Import dialog on desktop](images/import-1440-before.png) | ![Proposed Import dialog on desktop](images/import-1440-after.png) |
| Dark phone, 360 x 844 CSS px | ![Current dark Import dialog](images/import-dark-360-before.png) | ![Proposed dark Import dialog](images/import-dark-360-after.png) |

### Queue: report unavailable data accurately

Related: UI-01, UI-13. Replace the false idle claim with an unavailable state and a local retry. Keep the separate queue-control and queued-episode failures visible.

| View | Current | Proposed |
| --- | --- | --- |
| Phone, 390 x 844 CSS px | ![Current Queue failure on phone](images/queue-390-before.png) | ![Proposed Queue failure on phone](images/queue-390-after.png) |
| Desktop, 1440 x 1000 CSS px | ![Current Queue failure on desktop](images/queue-1440-before.png) | ![Proposed Queue failure on desktop](images/queue-1440-after.png) |

### Downloads: paired dates at a narrow phone width

Related: UI-10. This comparison changes only the date-range grouping. Both fields remain the existing DateField component.

| Current, 360 x 844 CSS px | Proposed, 360 x 844 CSS px |
| --- | --- |
| ![Current stacked Downloads dates](images/dates-360-before.png) | ![Proposed paired Downloads dates](images/dates-360-after.png) |

### Settings: a terminal failure state instead of repeated skeletons

Related: UI-01. Keep the page title and explain why editing is unavailable. The proposed error screen replaces the loading branch only; it is not a redesign of the settings sections.

| View | Current | Proposed |
| --- | --- | --- |
| Phone, 390 x 844 CSS px | ![Current Settings failure on phone](images/settings-390-before.png) | ![Proposed Settings failure on phone](images/settings-390-after.png) |
| Desktop, 1440 x 1000 CSS px | ![Current Settings failure on desktop](images/settings-1440-before.png) | ![Proposed Settings failure on desktop](images/settings-1440-after.png) |

## Initial screen-by-screen repair coverage

| Surface | Required work | Runtime limitation |
| --- | --- | --- |
| Global shell/navigation | UI-03, UI-06, UI-13; active-route semantics, shortcut policy, conditional status space | Active jobs and installed-device safe areas not exercised |
| Dashboard/grid/list | UI-01, UI-05, UI-08, UI-11, UI-12; retry, targets, deletion, action parity, visible errors | Populated cards and bulk operations source-reviewed |
| Add Feed/subscribed/local/recents/OPML | UI-04, UI-05, UI-12; targets, field/error associations, import outcome consistency | Subscribe form rendered; successful creation/import unavailable |
| Feed detail/settings/local uploads | UI-02, UI-05, UI-08, UI-09, UI-12; confirmations, save policies, field recipes | Source-reviewed without a feed record |
| Episode detail/transcript/ad editor | UI-02, UI-03, UI-05, UI-09; modal behavior, shortcuts, draft safety, coarse-pointer controls | Real audio, transcript selection, and save/re-cut unavailable |
| Audio cues/cross-episode scan | UI-02, UI-03, UI-05; shared modal behavior, focus ownership, target sizing | Source-reviewed; scan and nested editor not run |
| Patterns/Detected Ads/Ad Review/Cleanup | UI-01, UI-02, UI-05, UI-06, UI-08, UI-12; async states, tab contract, import semantics | Patterns shell and import dialog rendered; populated review cards unavailable |
| Sponsors | UI-01, UI-02, UI-05; local retry and correctly named confirmation | Error screen rendered; editing/deletion source-reviewed |
| History/Downloads | UI-01, UI-05, UI-06, UI-10, UI-11, UI-12; paired dates, mobile sorting, export/failure detail | Filter and failure screens rendered; populated records unavailable |
| Stats/spend/provider usage | UI-04, UI-11, UI-17; contrast, populated-table fitting, numeric alignment | Error/retry regions rendered; populated charts and drilldowns unavailable |
| Search/palette | UI-01, UI-03, UI-06, UI-07; truthful errors, shortcut scope, labels, result routing | Empty and failed requests exercised |
| Settings/security/provider configuration | UI-01, UI-05, UI-09, UI-12, UI-13; terminal failures, save ownership, safe fixed bars | Sustained failure exercised; saved values and provider mutations unavailable |
| Login/PWA recovery | UI-04, UI-09, UI-12; contrast, announced errors, draft-safe updates | Login form rendered after auth lookup failure; successful login/update unavailable |

## What to add to the design guide

| Contract | Required content | Owning code |
| --- | --- | --- |
| Control sizing | Named sizes; coarse-pointer minimums; icon versus hit-box dimensions; explicit exceptions | buttonStyles, fieldStyles, CopyButton, Pagination, SegmentedToggle |
| Responsive behavior | Shell breakpoint; per-table column priorities; card/table parity; narrow and short viewport behavior | Layout, table/card callers |
| Async states | Initial loading, empty, populated, failed, stale, pending mutation, partial success, retry | Query consumers and shared status/error presentation |
| Dialogs | Name, initial focus, trap, inert background, restore, scroll lock, nested Escape, dirty close | Modal and custom editor wrappers |
| Keyboard | Tabs, menus, comboboxes, shortcuts, native-control exclusions, modifier and repeat handling | Shared widgets and editor handlers |
| Forms | Label/help/error associations; input recipes; draft and immediate-save policies; inheritance/reset semantics | Existing field primitives and settings sections |
| Feedback | Inline error location; persistent failure detail; polite status announcements; copy/export outcomes | CopyButton, SavedBadge, error disclosures |
| Destructive actions | Which actions need explicit confirmation; object/scope; cancellation; duplicate-submit prevention | ConfirmModal, import/delete/reset callers |
| Theme contrast | Permitted foreground/background pairs and state combinations; text and non-text checks | index.css, themes.ts, themes.test.tsx |
| Layers and viewport | Named overlay levels; top/bottom occupied areas; safe-area and virtual-keyboard behavior | Layout, Modal, Popover, GlobalStatusBar, save bar |
| Motion | Reduced-motion alternatives for loading, scale, disclosure, and scroll | Global CSS and animated primitives |
| Review ownership | Authoritative source, reviewed commit, exception process, required browser evidence | Design guide, CI, PR template |

Recommended wording for the new rules:

1. An empty-state claim requires a successful data response. A failed request must retain context and expose recovery.
2. Every dialog has a required accessible name and uses the shared focus/dismissal behavior.
3. Coarse-pointer targets use the shared 44 CSS px minimum. Visual icons may remain smaller.
4. A table and its card version expose the same operations. Document any deliberate information reduction.
5. A date range remains one paired control group at the narrowest supported width.
6. Each settings section declares when it saves. Unsaved drafts survive navigation/update paths or require an explicit discard decision.
7. Destructive confirmation names the affected item and consequence and remains available until a decision.
8. Theme changes pass the complete permitted-pair matrix before release.
9. Guide examples come from production components or carry an explicit non-authoritative label.
10. UI changes include relevant browser, keyboard, and screenshot evidence in their PR.

## Implementation sequence and regression checks

| Order | Work | Why this order | Required proof |
| --- | --- | --- | --- |
| First | UI-01, UI-02, UI-03, UI-08, UI-09 | Prevent misleading operational state, accidental actions, and lost edits | Failure/retry, dialog keyboard, native-control shortcut, delete/reset, and dirty-draft flows |
| Next | UI-04, UI-05, UI-06, UI-12 | Fix shared primitives before their callers diverge again | Contrast matrix; rendered target sizes; accessible names/states; tab behavior |
| Then | UI-07, UI-10, UI-11, UI-13, UI-14, UI-17 | Apply the contracts to page layouts and device behavior | Mobile/desktop comparisons, action parity, safe areas, reduced motion |
| Alongside each change | UI-15 and UI-16 | Make the documented rule, implementation, and regression check agree | Updated live guide example and CI check for each repaired defect family |

Required validation matrix for the implementation:

| Dimension | Cases |
| --- | --- |
| Phone widths | 320, 360, and 390 CSS px; portrait and short landscape |
| Intermediate widths | 640, 768, and 1024 CSS px; especially tables and menus |
| Desktop widths | 1280 and 1440 CSS px; keyboard and mouse |
| Engines/devices | Chromium and WebKit; physical iOS check for keyboard and safe areas |
| Theme coverage | Every theme/mode for contrast; representative light, dark, and Obsidian rendered screens |
| Data | Empty, populated, long text, large counts, partial results, loading, failed, stale, and permissions/unavailable states |
| Interaction | Keyboard-only, touch, browser zoom/reflow, reduced motion, nested dialogs, background updates |
| Persistence | Filter changes, tab changes, dirty forms, retry, navigation away/back, refresh, PWA update |

## Populated reproduction

Use a disposable copy named `podcast.db` in its own directory. The launcher clears the copied login password, holds the background-leader lock, blocks external sockets, and rejects mutation requests. Never point it at a production data directory. It requires the application's Python dependencies.

```sh
MINUSPOD_AUDIT_DATA=/path/to/disposable-copy PYTHONPATH=src python3 docs/audits/ui-ux-2026-10-16/serve-backup.py
```

With Vite running against that local backend:

```sh
node docs/audits/ui-ux-2026-10-16/populated.cjs --after
node docs/audits/ui-ux-2026-10-16/implemented.cjs
```

Raw populated text dumps and unselected screenshots stay local. The published measurement file excludes record bodies. The image manifest lists the selected evidence; it excludes download client addresses and full-page settings captures. Missing audio and artwork reflect the database-only backup.

## Reproduction and artifacts

The capture scripts expect the repository's Vite server at `http://127.0.0.1:5173/ui/`, installed frontend dependencies, local Chrome, and the repository's Playwright dependency. The failed-request captures intentionally require no backend at the default proxy target.

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

From a separate shell at the repository root:

```sh
node docs/audits/ui-ux-2026-10-16/inspect.cjs
node docs/audits/ui-ux-2026-10-16/inspect.cjs --previews --skip-settings
node docs/audits/ui-ux-2026-10-16/inspect.cjs --checks
node docs/audits/ui-ux-2026-10-16/inspect.cjs --settings
node docs/audits/ui-ux-2026-10-16/contrast.cjs --after
```

Browser timing and transient loading content can vary. The JSON captures record what was present; only the targeted checks establish specific behavior. The initial small-control inventory can include descendants of closed details elements, so the findings use visible controls verified in the later captures.

| File | Purpose |
| --- | --- |
| [implemented-gallery.html](implemented-gallery.html) | Actual implemented screenshots, including populated before/after pairs |
| [populated.cjs](populated.cjs) | Real-data capture without presentation changes |
| [serve-backup.py](serve-backup.py) | GET-only audit launcher; requires `MINUSPOD_AUDIT_DATA` pointing to a disposable copy directory containing `podcast.db` |
| [REPORT.md](REPORT.md) | Findings, priorities, guide changes, and acceptance criteria |
| [gallery.html](gallery.html) | Before/proposed-after image comparisons |
| [inspect.cjs](inspect.cjs) | Route and width sweep; entry point for targeted captures |
| [previews.cjs](previews.cjs) | Browser-only visual proposals; no application-source edits |
| [checks.cjs](checks.cjs) | Date, tab, modal, shortcut, and dark-theme checks |
| [settings-proof.cjs](settings-proof.cjs) | Sustained settings failure observations and recovery proposal |
| [contrast.cjs](contrast.cjs) | Reproducible calculations from the current theme source |
| [validation.txt](validation.txt) | Verification results and remaining limits |
