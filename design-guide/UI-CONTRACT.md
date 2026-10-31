# UI behavior and review contract

Reviewed: 2026-10-30. Applies to desktop, phone, and tablet interfaces.

The HTML guide contains visual examples. Production components own behavior. Change a shared component when a rule applies across screens; do not patch each caller with a different recipe.

## Sources and checks

| Contract | Source | Required check |
| --- | --- | --- |
| Theme values | `frontend/src/index.css`, `frontend/src/themes/themes.ts` | `npm run design:sync`; `npm run design:check`; theme contrast tests |
| Page headers and summary cards | `pageStyles.ts`, Stats `StatCard` | Compare title, action placement, label/value alignment at every supported width |
| Buttons and fields | `buttonStyles.ts`, `fieldStyles.ts`, `rowActionStyles.ts`, `Modal.tsx` footer recipes, global coarse-pointer CSS | Measure the clickable element in a browser |
| Dialogs | `Modal.tsx`, `useDraftClose.tsx` | Name, Tab cycle, Escape ownership, focus restoration, discard flow |
| Page tabs | `PageTabs.tsx` | Arrows, Home/End, selected state, panel relationship |
| Async failures | `QueryError.tsx` and query consumers | Failed requests cannot render an empty-success claim |
| Drafts and updates | `useUnsavedChanges.ts`, `App.tsx`, `main.tsx` | Navigation, unload, pull-to-refresh, explicit update application |
| Browser contracts | `frontend/e2e/ui-contract.spec.ts`, `frontend/e2e/populated-ui.spec.ts` | `npm run test:browser` |

Run these commands from `frontend`. The guide's theme catalog and default tokens are generated from application source. CI fails when the generated values differ. Static HTML examples are illustrations, not substitutes for exercising the React components.

## Responsive layout and targets

- Use at least 44 x 44 CSS px for standalone controls on coarse pointers, including tablets. An icon may be smaller. Padding outside the clickable element does not enlarge its target. On coarse pointers the global CSS makes the 44px height a hard floor. The 44px width is a base-layer default that any `min-w-*` utility overrides, so a flex row can give a button its own wider floor.
- Inline text links and range-slider tracks are exceptions to the square target rule. Checkbox and radio measurements use their associated clickable label, not an intentionally hidden native input.
- Preserve action and sorting access when tables become cards. Processing History and episode costs use cards below 1280 CSS px. Downloads uses cards below 768 CSS px and progressively reveals columns.
- Choose table breakpoints from populated content. Check long titles, large counts, IPv4 and IPv6 values, and error messages. Routine lists must fit their containers. Detailed comparison tables may scroll within a named, keyboard-focusable region.
- Keep Since and Until together in a two-column group at 320 CSS px. Each field has a label and reciprocal date limit. Date-only filters must retain their date-only semantics.
- Navigation collapses below 1280 CSS px. Keep the active route marked with `aria-current="page"` in both navigation layouts.
- Verify 320, 390, 466, 626, 768, 890, 1024, 1280, and 1440 CSS px for changed layouts. Include a short viewport when changing dialogs or fixed bars. 466 and 626 (portrait) and 890 (landscape) match a folded and an unfolded foldable phone.
- The Feeds grid has two columns from 600 CSS px. Stat and tile grids use even column counts between 640 and 1023 CSS px.
- The page shell, fixed bars, and floating buttons pad for `env(safe-area-inset-left/right)` through the `.safe-x` and `.safe-x-wide` utilities in `index.css`.
- Under `@media (horizontal-viewport-segments: 2)` dialogs stay in the right-hand viewport segment.
- Overlapping cut markers on the episode page render as one block card with a segments toggle. Member cards keep their own actions.

## Buttons

There is no `<Button>` component. Recipes are class strings composed at the call site as `${btnBase} ${btnPrimary}`. The base carries size, radius, gap, disabled state, and focus ring, so call sites add only layout classes such as `w-full`.

| Recipe | Use | Notes |
| --- | --- | --- |
| `btnBase` | Size and shape for every standard button (12 px by 6 px padding, 4 px radius) | Always paired with one colour variant |
| `btnLg` | Page-level form actions (Save settings, Add feed, Create feed, dialog primary on wide forms) | Same as `btnBase` with 16 px by 8 px padding |
| `btnPrimary` | The one main action in a card, row, or dialog | One per group |
| `btnSecondary` | Default action beside a primary one | Fill, no border |
| `btnOutline` | Same as secondary | Alias kept for call-site intent |
| `btnGhost` | Low-emphasis action, icon toggles | Muted text, accent hover |
| `btnDestructive` | The primary action is destructive (confirm delete) | Solid fill |
| `btnDestructiveOutline` | Destructive action beside a primary one (Delete beside Edit) | Tinted border and hover |
| `touchTarget` | Standalone control that needs a 44 px box on a phone | `min-h-11 min-w-11` below sm |
| `touchCompact` | Icon-only header action beside a heading | 36 px square below sm, text button from sm |
| `touchTargetRow` | Row that must stay on one line on a phone | Full height, shared width below sm |
| `rowActionBtn`, `rowActionIcon` | Marker and detection row actions and the icon beside them | Icon derives its box from the same source. 44 px tall on phones, labels never wrap. Four controls share one line; the five-control Ad review card moves Split and Edit to a second line below 430 px |
| `tableActionBtn` | Desktop detection table rows | 30 px tall; carries `focusRing` |
| `cardActionBtn` | Review and detection card actions | 44 px floor; labels wrap on a phone; carries `focusRing` |
| `dialogFooterBtn` | Dialog footer action | Text label at every width. Derives from `btnBase` as `${btnBase} border border-transparent` |
| `dialogFooterGrid` | Dialog footer with 3 or more actions | Two columns below 360 px, one row of equal columns from 360 to 639 px, flex row from sm |
| `iconTarget` + `iconVisual` | Icon button with a smaller filled square (AuditionPlayButton, OpenEditorButton) | 44 px hit box on coarse pointers; visual fills the 44 px target below sm, 28 px from sm |

- Every button recipe carries `focusRing` and the disabled state through `btnBase` (or `btnLg`). `rowActionBtn`, `cardActionBtn`, and `tableActionBtn` carry `focusRing` too.
- Pair `iconTarget` with `iconVisual`. The focus ring sits on the visual through `group-focus-visible`. `AuditionPlayButton` is the reference and is one size everywhere; `OpenEditorButton` is the second.
- An icon-only control centers its icon with inline-flex items-center justify-center or the iconTarget and iconVisual pair. Padding alone places the icon top-left once the coarse-pointer floor stretches the box.
- Dialog footers with 3 or more actions keep their text labels at every width. Below 360 CSS px they become two equal columns in two rows, and from 360 to 639 px one row of equal-width columns (dialogFooterGrid), so nothing is icon-only. PatternDetailModal is the reference.
- Inline link-style actions (`text-primary hover:underline`, "Show full match") and selected-state toggles (`PageTabs`, `SegmentedToggle`, pagination numbers) are intentionally not button recipes.

### Media player

`EpisodePlayer` (with `useEpisodePlayer`) is the one episode audio player. Reuse it; do not add a second player or restyle the native `<audio>` element.

- Layout, top to bottom: chapter row (only when the episode has chapters), scrubber, time row, transport row.
- Chapter row: `ChevronLeft` and `ChevronRight` ghost buttons around the current chapter title, truncated and centered. Previous and next disable at the first and last chapter.
- Scrubber: a range input with a tick at each chapter start after 0. Elapsed time sits left and remaining time right as `-m:ss` (`formatTimestamp`).
- Transport: back 15 seconds, play or pause, forward 30 seconds. Center this group independently of speed. Below 336 CSS px of container content width, put speed on a second centered row. At wider sizes, place it at the right edge. Keep the Gauge icon visible at every width.
- Icons: `RotateCcw` and `RotateCw` at 32 px with the seconds number centered inside the circle, `Play` and `Pause` at 24 px, chapter chevrons at 20 px.

| Control | Recipe | Size |
| --- | --- | --- |
| Play and pause | `${btnBase} ${btnPrimary}` as a filled circle (`rounded-full`) | 56 px at every width |
| Back and forward | `${btnBase} ${btnGhost} ${touchTarget}` with `h-11 w-11 shrink-0 px-0` | 44 x 44 CSS px at every width |
| Speed and chapter chevrons | `${btnBase} ${btnGhost} ${touchTarget}` | 44 px on coarse pointers, 32 px tall on desktop with a fine pointer |
| Container | `@container rounded-lg border border-border bg-card` with `focusRing`, `tabIndex={0}`, `role="group"` | `p-3`, `p-4` from sm |
| Seek range | 44 px tall hit box, `focusRing` | Full width |

- Previous chapter restarts the current chapter when the playhead is more than 3 s into it; otherwise it goes to the previous chapter.
- Speed cycles 1x, 1.25x, 1.5x, 1.75x, 2x, 0.8x and wraps. The choice persists under the `minuspod.playbackRate` localStorage key and applies to every episode.
- With focus on the player container, Space toggles play and Left and Right skip back 15 and forward 30 seconds. Ignore controls, editable content, repeated keys, composition, modifiers, and handled events.
- The scrubber seeks on release. The time row and chapter title follow the drag; commit on pointer up, key up, or blur. Pointer cancellation discards the preview.
- Disable seek, skip, and chapter navigation until duration is known. Use "-" for unknown remaining time. Reset the timeline when the media source empties.
- Announce buffering as "Loading audio...". Failed media shows "Could not play this audio." and Retry playback. Chapter failures have their own Retry chapters action without restarting audio.
- Check every speed at narrow widths, especially 1.75x. Measure control bounds against the player, not just the document width. The play button stays on the container's center line.
- The episode description sits under the player in `ExpandableText` with `toggleText` "Show more" and "Show less". The control appears only when the text overflows.
- Focusing a link inside collapsed rich text expands it and scrolls the link into view. Keyboard focus must never remain on clipped content.

### Shared field, badge, and component recipes

- Fields: `inputBase`, `selectBase`, and `textareaBase` share one chrome (4 px radius, `bg-background`, `border-input`, `text-foreground`) and inputs and selects share one 40 px minimum height. Textareas size by rows. `labelBase` is the form label; `filterLabel` stays the muted label in filter rows.
- Badges: `badgeBase` is the one badge shape (12 px, medium weight, no wrap) and takes a `tint`. `countPill` is the round count pill and also takes a tint.
- `StatusBadge` renders an episode status with its shared color and label. Use it instead of composing `badgeBase` with `displayStatusColor` by hand. `short` drops qualifiers such as "permanently" and "(offline)".
- `StatTile` is the metric card: label above value, optional hint below, `h-full` so tiles in a grid row stretch to equal height. `tone` colors the value only.
- `CloseButton` is the one close X. It uses the `iconTarget` and `iconVisual` pair, so it has a 44 px hit box on phones.
- Numbers: `formatPercent` prints a whole-number percent with no decimals, and the same quantity uses the same label text in every place. Use the clock form `m:ss` (`formatTimestamp`) for positions, `Xm Ys` (`formatElapsed`) for elapsed or summed durations, and tenths of a second only in the editor scrub readout.

## Copy

- Use sentence case for headings, buttons, tabs, menu items, labels, and dialog titles. Product nouns keep their capitals (Podping, LLM, OPML, RSS, API, URL, DAI, Whisper).
- Error text is "Could not <verb> <noun>." with a period. Add "Try again." only when a retry control sits beside it.
- Empty states are "No <things> yet." for lists the user fills, "No <things> match these filters.", and "No results for "<query>"." Always end with a period and put the primary action beneath where one exists.
- A held marker is "Needs review" (badge, filter option, row label). The episode page section is "Held for review (n)". Cleanup suggestions keep "Pending".
- "Confirm" and "Not ad" are the only marker decisions. "Approve" and "Reject" apply only to cleanup suggestions.
- Re-running detection is "Re-detect ads". The full pipeline is "Full reprocess".
- Use `reprocessMenuCopy` for episode and feed menus. YouTube actions describe SponsorBlock, patterns, captions, and the configured fallback. They must not promise an RSS-only AI pass.
- A nav label equals the page `h1`: "Feeds", "Add feed", "Patterns". Back links read "Back to feeds".
- A destructive confirm button repeats the verb and noun ("Delete pattern", "Delete 3 episodes") under a sentence-case question title ("Delete 3 episodes?"), not "Yes, delete".
- Adding is "Add <noun>" with the lucide Plus icon and no typed "+"; the pending state keeps the verb ("Adding feed..."). "Create" is only for objects the app generates.
- Cancel discards edits. Close is for a dialog with nothing pending; there is no "Done". Cue candidates use "Hide". "Dismiss" is only for a notice removed for good.
- Show confidence and scores through `formatPercent` with no decimals and the label after the number ("99% confidence", "87% match"). Positions use `m:ss` (`formatTimestamp`); elapsed or summed time uses `formatElapsed` ("2m 5s").
- Show timestamps through `formatDateTime`, with "-" for a missing value. Run logs keep UTC and label it.
- An accessible name contains the visible label. Use one title or `aria-label` per control, not two with different text.
- Use "and", not "&", in headings, labels, and sentences. The only exception is the editor footer's "Confirm & next".

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

YouTube overrides wait for global settings before displaying inherited values. A failed query shows Retry, never an assumed "off" value. Numeric overrides show units and reject values outside the server's integer bounds. An explicit custom value may equal the current global value.

Add feed names RSS and YouTube in its input label. Advanced options apply to either source. Discard lookup results when the input changes, even if an aborted request later resolves.

## Dialogs and keyboard

`Modal` requires `ariaLabel` or `ariaLabelledBy`. Prefer a visible heading. Custom waveform editors use the same focus and Escape hooks.

- `Modal` takes `header` and `footer` props. The panel is a flex column and only the body scrolls. The header and footer stay pinned and inside the viewport at 320 to 390 CSS px. `dialogFooterBtn` derives from `btnBase`.
- The three dialogs that do not use `Modal` (ad editor, cue mark, cross-episode scan) apply the same `data-split` layout: pinned header and footer, scrolling body.

- Move focus inside on open; contain forward and reverse Tab, including any owned portaled controls.
- Make background content inert and lock background scrolling. Restore both when the dialog closes.
- Only the top dialog owns Escape. A popover inside it closes first. Return focus to the trigger if it still exists. Capture activation as well as focused elements: WebKit may leave a clicked button unfocused.
- Place dialogs above status and save bars; place their popovers above the dialog. Keep the panel inside the dynamic viewport and safe-area insets.
- Do not close data-entry dialogs on backdrop clicks. Dirty editors use `useDraftClose`; successful saves close without a discard prompt.
- Use Ctrl/Cmd+K for global quick search. Ordinary printable keys remain available to native controls, browser search, and assistive technology.
- Editor shortcuts run only when focus belongs to the editor. Ignore editable content, buttons, selects, sliders, composition, repeated keys, modifiers, and already-handled events.
- Use `PageTabs` for page sections with exclusive panels. The `wrap` prop keeps a short tab set inside the viewport on phones (Search, six tabs). In-page section links (Stats) are anchors styled with `tabStrip` and `tabItem`, not `role="tab"`. The Add feed mode toggle is a `SegmentedToggle` that stacks below 560 px.
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

Feed metadata has Save metadata and Cancel actions. Show success beside the stable save label. Artwork uploads announce pending, success, and failure states. Disable conflicting operations while requests run, including cookie save/clear, key save/test, and settings save/reset.

Register unsaved state with `useUnsavedChanges` so router navigation, unload, pull-to-refresh, and app updates respect it. The global Save Changes button does not save independent sections. New independently saved sections must register their own drafts. `useDraftClose` handles explicit discard decisions in editors.

A service-worker update waits for the user's Reload to update action. Disable that action while registered drafts exist. A hidden tab is not proof that its work is saved.

Feed deletion, resets, and replace-mode imports use `ConfirmModal`. Name the object and consequence, provide Cancel, disable duplicate submissions, and retain errors for retry. Never require a second click within a timer. Do not translate a generic confirmation into a destructive default action.

## Contrast, hierarchy, layers, and motion

- Normal text requires at least 4.5:1 contrast against its rendered surface. Test ordinary text, muted text, status text, links, solid controls, hover states, and tinted badges across every theme and mode.
- Use the appropriate paired foreground token. Being token-based does not prove sufficient contrast. Disabled controls and decorative marks require separate interpretation; do not dilute active text with opacity.
- Card and section titles use `cardTitle` (18 CSS px, 600) and sub-headings use `subHeading` (14 CSS px, 600), always `text-foreground`. Data cards pad with `cardPad` (16 CSS px) and section cards with `sectionPad` (24 CSS px).
- Use one visible page `h1` at 24 CSS px, section headings at 18 CSS px, and consistent summary-card labels/values. Keep numeric column headings aligned with their values.
- Use `pageHeader`, `pageTitle`, and `pageDescription`: 24 CSS px titles, 16 CSS px header gaps, 24 CSS px bottom spacing, and actions below the title on phones.
- Episode header: metadata chips sit on one non-wrapping line and the action buttons on their own non-wrapping line. Compact the chips or buttons only below 361 CSS px.
- Counter cards place the value above the label, left aligned. Metric cards place the label above the value and may add details. Both use 20 CSS px bold tabular values, 12 CSS px labels, and 16 CSS px outer padding. Metrics nested within a larger card may share its surface.
- Summary cards in the same row align their values and label starts, including clickable cards. Use an explicit shared layout; native button centering must not shift content when a neighboring label wraps.
- Layer order: sticky navigation 30, ordinary popovers and save bars 40, status bar 50, dialogs 60, dialog-owned popovers 70. Nested dialogs follow DOM order within the dialog layer.
- The status bar reports its measured occupied height to the root shell, before the update notice and page content. Sticky navigation and the mobile menu use the same inset. Do not reserve an unconditional gap or assume that safe-area padding is zero.
- Respect `prefers-reduced-motion` for spinners, copied-state animation, transitions, and smooth scrolling. Keep loading and status information understandable without animation.

## Review evidence

A UI PR includes affected routes, widths, input modes, and before/after images from the real components. State whether it used actual records, failed requests, or proposals. Never present an edited screenshot as an implemented screen.

CI runs type checking, lint, unit tests, guide parity, and browser contracts. The browser suite runs Chromium and WebKit at 320, 390, 768, and 1440 CSS px. It uses an unavailable backend for failure flows and checks accessible form names with axe.

Set `MINUSPOD_POPULATED_URL` to a disposable instance URL ending in `/ui` for populated editor, player, and YouTube checks. Set `MINUSPOD_STORAGE_STATE` to a local Playwright authentication-state file when login is required. These checks use existing records, including a YouTube feed with a completed episode; CI skips them without a populated instance.

Keep backups, authentication state, subscriber addresses, and private configuration out of commits and published evidence. A database-only restore cannot verify successful audio playback. Abort missing-media requests during layout checks because requesting a missing processed file can enqueue processing. Record that limitation separately from layout and error-state results.

Record exceptions with the affected control, reason, owner, and follow-up issue. Browser emulation does not replace physical iOS checks for keyboards, safe areas, VoiceOver, or installed PWA updates.
