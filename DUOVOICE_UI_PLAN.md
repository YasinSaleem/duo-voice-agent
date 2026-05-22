# DuoVoice Tutor — UI Implementation Planning Document

**Document Type**: Frontend Design & Architecture Handoff  
**Version**: 1.0  
**Audience**: Frontend Engineers, UI/UX Designers, Engineering Lead

---

## 1. Product/UI Architecture Overview

### 1.1 UI Philosophy

DuoVoice Tutor is a real-time voice interaction product. The dominant experience — the active call session — is an audio-first environment where visual UI must stay out of the way while still communicating system state clearly. Every design decision should serve one of three functions: orient the user (where they are, what state the system is in), inform the user (what was said, what was corrected, what progress exists), or control the session (mute, pause, end, navigate).

The interface should follow a **functional product aesthetic**: high contrast, typographically clear, with a restrained color palette and purposeful density. Think Vercel's dashboard, Linear's workspace, or Raycast's launcher — interfaces that feel fast, competent, and built for repeated use, not for first-impression screenshots.

Avoid ambient decorative elements, animated backgrounds, or gradient-heavy containers that compete with live session state. Visual feedback must communicate real system states (connecting, speaking, listening, error) — not emulate them for aesthetics.

### 1.2 Layout Paradigm

The application uses a **three-mode layout** rather than a persistent navigation shell:

- **Auth mode**: Centered single-column card. No chrome.
- **Lobby mode**: Full-viewport two-zone layout. Fixed header, scrollable main content area. No sidebar by default; progress drawer slides in from the right on demand.
- **Session mode**: Full-viewport split-panel (two-column). Fixed layout with no scrollable chrome around it. The session takes full ownership of the viewport.

There is no persistent sidebar navigation because the application has a linear flow with only three primary destinations: Auth, Lobby, Session. Introducing sidebar navigation would add chrome that communicates false depth.

### 1.3 Navigation Model

Navigation is **flow-based, not menu-based**:

```
Auth Screen
    └── Lobby (Scenario Selection Dashboard)
            ├── Progress Drawer (slide-out overlay, right side)
            └── Active Session Playground
                    └── (on Complete) → Lobby + Progress Drawer auto-opens
```

Back navigation from the session to the lobby is only via the "Complete Session" action — no browser back button dependency. The progress drawer is not a route; it is a panel overlay that preserves the lobby state behind it.

### 1.4 Information Architecture Rationale

The hierarchy of information importance:

1. **Session state** (what is the system doing right now) — always visible during a session
2. **Conversation content** (what was said) — scrollable log, always accessible during a session
3. **Grammar feedback** (what the user got wrong) — surfaced inline, never in a separate modal
4. **Progress data** (historical performance) — available on demand via drawer, never interrupting active work
5. **Account/settings** (profile, logout) — header-level, low visual weight

---

## 2. Screen Inventory

### Screen 1: Auth / Login Screen

**Purpose**: Authenticate existing users and register new ones. Gated entry point.

**Primary Actions**:
- Submit sign-in credentials
- Submit new account registration

**Secondary Actions**:
- Toggle between Sign In and Create Account tabs

**Required Data**:
- Email field
- Password field
- Tab state (Sign In vs. Create Account)
- Submission loading state
- Toast feedback (success, error, warning)

---

### Screen 2: Lobby — Scenario Selection Dashboard

**Purpose**: Central workspace where users select a scenario, review connection health, and initiate a session.

**Primary Actions**:
- Select a scenario card
- Launch a session (Start Real-Time Session button)
- Open the Progress Drawer

**Secondary Actions**:
- Logout
- View API connection status

**Required Data**:
- Authenticated user email
- API connection status (live indicator)
- Scenario list (fetched dynamically): title, language/flag, difficulty, roleplay description
- Selected scenario state
- Session launch button state (disabled until selection)

---

### Screen 3: Active Voice Session Playground

**Purpose**: Real-time bilingual conversation workspace. Users practice spoken Spanish against an AI tutor.

**Primary Actions**:
- Mute/unmute microphone
- Pause/resume the tutor agent
- End session (Complete Session)

**Secondary Actions**:
- Expand/collapse grammar tip drawers on individual conversation bubbles
- Read live subtitles

**Required Data**:
- WebRTC connection status
- Active scenario name
- Live subtitle stream (tutor output, word-by-word)
- Conversation log (tutor bubbles, user bubbles, grammar flags)
- Grammar tip content per flagged user turn (learner said, corrected phrasing, explanation)
- Mic mute state
- Tutor pause state
- Visualizer animation state (connecting / speaking / listening / muted)

---

### Screen 4: Learning Progress Drawer

**Purpose**: On-demand historical tracker. Shows vocabulary, grammar focus areas, and session summaries.

**Primary Actions**:
- Refresh/sync data manually

**Secondary Actions**:
- Close drawer
- Read vocabulary chips, grammar cards, session memory cards

**Required Data**:
- Sessions completed count
- Vocabulary words learned count
- Vocabulary list (Spanish word + English translation per chip)
- Grammar focus areas (topic, status: Proficient/Needs Work, description/bullet points)
- Session history timeline (scenario name, timestamp, AI summary, key takeaways)
- Sync loading state
- Empty states for all three data regions

---

## 3. Detailed Screen Layout Specifications

### 3.1 Auth Screen

**Layout**: Full-viewport centered column. No header, no footer, no navigation.

**Composition**:
- Vertically and horizontally centered card container (max-width: 400px, fluid on mobile)
- Above the card: product wordmark and optional tagline in small subdued text
- Card contents (top to bottom):
  1. Tab bar: "Sign In" | "Create Account" — full-width tabs, not pills
  2. Email input with label above (not placeholder-as-label)
  3. Password input with label above, with show/hide toggle icon inside the field
  4. Submit button — full-width, primary style
  5. Inline form error text (below the relevant field, not a modal)
- Toast notification anchor: top-center of the viewport, z-indexed above everything

**Validation Behavior**: Errors appear below the respective field on blur, not on submit only. Submit button disables and shows a spinner on submission. Fields become read-only during submission.

**Responsive**: Card is 90% width on viewports below 480px with 24px horizontal padding. Tabs remain full-width.

---

### 3.2 Lobby — Scenario Selection Dashboard

**Layout**: Full-viewport single-column with a fixed header and scrollable main content.

**Header** (fixed, 56px height):
- Left: Product wordmark
- Center: API status indicator — a labeled badge with a small solid dot (green = connected, red = disconnected, amber = degraded). Badge includes a short status label ("API Connected", "Offline"). This is the only status communication; it must always be visible.
- Right: User email in subdued secondary text, then "View Progress" button (outlined, not primary), then "Log Out" text link (destructive-subtle style, never a big button)

**Main Content**:
- Container: max-width 1024px, centered, top padding of 48px
- Section heading: "Select a Scenario" (heading-2 weight), followed by a single-sentence description (subdued body text)
- Scenario grid: 3-column responsive grid (2-col on tablet, 1-col on mobile). Gap: 16px.
- Each scenario card:
  - Internal padding: 20px
  - Top row: language flag icon (16x16) + locale code (e.g., "es-ES") in secondary text — right-aligned difficulty badge (e.g., "Intermediate" — outlined pill, no fill)
  - Middle: Title in body-large weight
  - Bottom: Roleplay setting description in small secondary text (2-line clamp)
  - Selected state: 1.5px primary-color border, very subtle primary-color tint background (not a glow)
  - Hover state: border lightens one step, minimal background shift
- Action zone: right-aligned below the grid with 24px top margin. Contains only the "Start Real-Time Session" button. Disabled state is visually clear (muted, no cursor).

**No modals on this screen**. Progress drawer slides in from the right.

**Responsive**:
- Header collapses the user email on tablet; retains the status badge and action buttons
- Progress button becomes an icon-only button below 768px

---

### 3.3 Active Voice Session Playground

**Layout**: Full-viewport, two-column split. No scroll on the outer container. This screen takes full ownership of the viewport.

**Column split**: 45% / 55% on desktop. Stack to single-column on tablet/mobile (left column on top, right column below, full-height scrollable right column).

**Left Column — Call Chamber**:
- Background: slightly darker than the main canvas — creates a contained zone
- Vertically centered content stack (no flex stretching):
  1. **Scenario label**: small uppercase tracking text, subdued color (e.g., "TAPAS BAR IN BARCELONA")
  2. **WebRTC status text**: small label below the scenario ("Connecting...", "Listening...", "Tutor is speaking...") — this is a text-only status line, not a badge
  3. **Visualizer**: 160px diameter circular element, centered. Three concentric rings outside it. No decorative gradients inside the circle — use a flat fill with the state color. State behaviors:
     - Connecting: rings rotate slowly at low opacity
     - Speaking: rings scale outward rhythmically (CSS animation tied to an "active" class; not real audio data-driven in a first implementation)
     - Listening: rings are static, inner circle dim
     - Muted: inner circle uses the destructive/error color, all ring animation stops
  4. **Subtitle overlay**: appears only when tutor is speaking. 280px wide, centered below the visualizer. Contains a small "TUTOR IS SPEAKING" uppercase label at top and the streaming text below. Background: a solid low-opacity dark card — not frosted glass. Fades out with a 200ms opacity transition when hidden.
  5. **Control bar**: three buttons in a horizontal row, centered:
     - Mute Mic: icon-only button (mic icon / muted-mic icon), outlined, square 40px
     - Pause/Resume Tutor: text + icon button ("Pause Tutor" / "Resume Tutor"), outlined
     - Complete Session: text button, destructive variant ("Complete Session"), 40px height. Not an icon-only button — the label must always be readable.

**Right Column — Conversation Log**:
- Background: same as main canvas
- Fixed header row within the column: "Conversation & Grammar Logs" label (heading-6 weight) + a live sync badge on the right (solid dot + "Live Sync Active" label — dot pulses via CSS animation)
- Scrollable conversation timeline: fills remaining height. Custom scrollbar styles (thin, muted color).
- Conversation bubbles:
  - Tutor bubble: left-aligned, max-width 75% of column, labeled "Spanish Tutor" above in small secondary text. Background: muted surface variant color.
  - User bubble: right-aligned, max-width 75% of column, labeled "You" above (right-aligned). Background: primary color at reduced opacity (not full brand color — it should read clearly but not overpower).
  - Grammar badge inside user bubble: small inline button "Grammar Tip" appended below the bubble text. Outlined, warning/amber style. Not an emoji.
  - Grammar expansion: toggles a card directly below the user bubble (not a modal, not a tooltip). The card has three labeled rows:
    - "You said" — erroneous text in error-colored monospace or emphasized text
    - "Correction" — corrected text in success color
    - "Explanation" — plain body text

**No overlapping panels on this screen**. The drawer does not appear during an active session.

**Responsive**:
- Below 1024px: columns stack vertically. Left column fixed at 360px height (not full viewport). Right column takes remaining scroll space.
- Below 640px: visualizer scales to 120px. Control buttons relabel to icons only.

---

### 3.4 Learning Progress Drawer

**Layout**: Right-side slide-out panel. Width: 480px fixed on desktop. Full-width (100vw) on mobile. Behind it: a semi-transparent overlay covers the lobby (opacity 40% dark overlay, not blur — blur is expensive and not justified here).

**Drawer Header** (sticky inside drawer):
- Left: "Learning Progress" (heading-5)
- Right: Refresh button (icon + "Refresh" label, changes to icon + "Syncing..." with spinner on active) and a close icon button (X). Both at the same row level.

**Drawer Body** (scrollable):

Section 1 — Stats Row:
- Two stat cards side by side, equal width
- Each card: large number (heading-2 weight), label below in small secondary text ("Sessions Completed", "Vocabulary Learned")
- Simple border, no shadow

Section 2 — Vocabulary Cloud (labeled "Vocabulary Learned"):
- Flex wrap container of chips
- Each chip: "Spanish (English)" — 13px, bordered, muted background, no fill
- On hover: background lightens, cursor default (chips are read-only; no click action required unless a later iteration introduces it)
- Empty state: centered single-line secondary text "No vocabulary recorded yet."

Section 3 — Grammar Focus Areas (labeled "Grammar Focus"):
- Vertical stack of cards, 12px gap
- Each card: title row with topic name (body weight) and a status badge (right-aligned) — "Proficient" in success color outline pill, "Needs Work" in error color outline pill
- Below title: bullet list of description points in small secondary text
- Empty state: "No grammar data available yet."

Section 4 — Session Timeline (labeled "Session History"):
- Vertical stack of memory cards
- Each card:
  - Top row: scenario name (body-medium) + timestamp (small secondary, right-aligned)
  - AI summary paragraph (small, secondary, 3-line clamp expandable on click)
  - Key takeaways: bulleted list in small text
- Empty state card: centered message "No sessions completed yet. Start learning to record your first memory." — not a full illustrated empty state, just a subdued text block inside a dashed-border card.

---

## 4. Component Design System Plan

### 4.1 Buttons

**Variants**:
- `primary` — solid fill, high contrast. Used for the main single CTA per screen (e.g., "Start Real-Time Session", "Sign In").
- `secondary` / `outlined` — border only, transparent fill. Used for secondary actions ("View Progress", "Pause Tutor", "Mute Mic").
- `destructive` — uses error/red token for fill or border depending on severity. "Complete Session" is destructive-solid. "Log Out" is destructive-ghost (text only, no border).
- `ghost` — no border, no fill. For icon-only controls (close X, refresh icon).
- `loading` — any variant can take a loading state: spinner replaces or prepends label, button disables.

**Sizes**: `sm` (32px height), `md` (40px height, default), `lg` (48px height).

**Rules**:
- Icon-only buttons must have an accessible `aria-label`.
- No icons without purpose. No decorative icons prepended to text buttons unless they meaningfully aid scan (e.g., mic icon on mute button).
- Button text is always sentence case, never ALL CAPS.
- Disabled state: 40% opacity, no cursor pointer, no interaction.

---

### 4.2 Inputs

**Variants**: `text`, `password` (with show/hide toggle), `email`.

**Anatomy**: Label above (always visible, never placeholder-as-label), input field, helper/error text below.

**States**: default, focus (primary color border), error (error color border + error text below), disabled (muted opacity), read-only.

**Rules**: No floating labels. Labels are static. Error messages appear below the field and are associated via `aria-describedby`. No inline icon decorations unless functional (e.g., show/hide password toggle).

---

### 4.3 Cards

**Variants**:
- `scenario-card` — interactive, selectable. Selected state: primary color border, subtle primary tint background.
- `stat-card` — display-only. Large number, small label.
- `grammar-card` — expansion card inside conversation bubbles. Three fixed rows. Not interactive except for the toggle trigger on the bubble.
- `memory-card` — session history entry. Contains scenario name, timestamp, AI summary, key takeaways.
- `grammar-insight-card` — grammar focus area in the drawer. Contains title, status badge, bullet list.

**Rules**: Cards use a consistent border radius (see Design Tokens). No drop shadows on interactive cards — use border changes for state instead. Shadows only on elevated surfaces (drawers, modals).

---

### 4.4 Badges / Pills

**Variants**:
- `status-dot` — small dot with adjacent label for API connection status and Live Sync indicator. Dot uses semantic color. Animated pulse variant for "live" states (CSS `@keyframes` on opacity or scale).
- `difficulty-badge` — outlined pill on scenario cards. Color depends on difficulty level (e.g., Beginner: neutral, Intermediate: warning, Advanced: error).
- `grammar-status` — "Proficient" in success-color outline pill, "Needs Work" in error-color outline pill.
- `grammar-tip` — amber/warning-color outlined small button inside user bubbles. Reads as a button, not a decorative chip.

**Rules**: Pills are never filled with saturated color for decorative reasons. Fill is reserved for primary actions or hard error states. Pill text is always sentence case.

---

### 4.5 Toast Notifications

**Placement**: Top-center of the viewport, stacked if multiple. z-index above all other content.

**Variants**: `success`, `error`, `warning`, `info`.

**Anatomy**: Icon (semantic, not decorative) + message text + optional close button. No titles; message should be self-contained in one sentence.

**Behavior**: Auto-dismiss after 4 seconds. Error toasts stay until dismissed. Stack vertically with newest on top. Animate in from top with a short translate + opacity transition. Animate out to top on dismiss.

**Rules**: Toast text must be human-readable, not technical error codes. "Invalid email or password" not "Error 401: Unauthorized".

---

### 4.6 Tabs

**Usage**: Auth screen only (Sign In / Create Account). Potentially reusable for future screens.

**Anatomy**: Tab container full-width of parent, two tab items. Active tab: bottom border highlight (primary color, 2px) + full-weight text. Inactive: subdued text, no border.

**Rules**: No pill-style tabs. Line-style underline tabs are cleaner for form contexts. Tab switching is instant — no transitions required.

---

### 4.7 Conversation Bubbles

**Purpose**: Render turn-by-turn dialogue in the session log.

**Tutor Bubble**:
- Left-aligned block
- Max-width: 75% of column
- Label row above: "Spanish Tutor" in 11px secondary text
- Body text: body-sm size

**User Bubble**:
- Right-aligned block
- Max-width: 75% of column
- Label row above: "You" in 11px secondary text, right-aligned
- Body text: body-sm size
- Grammar tip button below text, right-aligned within bubble

**Grammar Expansion**:
- Rendered below the user bubble, full column width (not bounded by bubble width)
- Three labeled rows with semantic text colors
- Dismiss by clicking the grammar tip button again (toggle)

**Rules**: Bubbles never use avatar images. Label text is sufficient for attribution. No timestamps per bubble (too noisy in a real-time context). Scroll to bottom on new message append.

---

### 4.8 Visualizer Component

**Purpose**: Communicate audio session state visually. This is the primary ambient feedback mechanism during a session.

**States and rendering**:
- `connecting` — three concentric rings, low opacity, slow rotation animation
- `active/speaking` — rings have a scale-out animation, periodic and rhythmic
- `listening` — rings static, inner circle at neutral color
- `muted` — rings stopped, inner circle switches to error color token

**Implementation note**: The animation is CSS class-driven (state machine: add/remove class on the container). It does not need to respond to actual decibel data in the initial implementation. The class toggles provide sufficient behavioral differentiation.

**Rules**: The visualizer must never be purely decorative. Every visual state maps to a real system state. Adding "idle breathing" animations when the system is not in an active state is acceptable only if it is clearly distinct from the "active" state.

---

### 4.9 Empty States

**Usage**: Vocabulary cloud, grammar focus list, session history in the drawer.

**Anatomy**: Short, plain secondary-color message. No illustrations, no icon decorations. Optionally a dashed border card container for session history (to communicate that something will eventually go here).

**Rules**: Empty state text must be specific to the section, not generic ("No data available"). It should tell the user what will appear and how to trigger it.

---

### 4.10 Loading States

**Spinner**: Simple CSS ring spinner. Used inside buttons and next to the "Syncing..." drawer label.

**Skeleton loaders**: Use for the scenario grid on initial load (3 card-shaped skeletons in the grid, animated shimmer). Not used elsewhere — other loads are fast enough for a simple spinner.

**Rules**: Never show a full-page loading screen after auth. Skeleton loaders in the grid are sufficient. The API status badge communicates connectivity.

---

### 4.11 Modals / Dialogs

**Usage**: Minimal. No modals are introduced where inline or drawer patterns work. The only appropriate modal would be a confirmation dialog for destructive actions if needed in later iterations.

**If required**: Centered overlay, semi-transparent backdrop, max-width 480px, heading + body text + two buttons (confirm destructive, cancel secondary). No nested scroll inside modals.

---

## 5. Design Tokens / Visual System

### 5.1 Typography

**Scale**:
- `display` — 32px / 1.2 line-height / weight 600
- `heading-1` — 28px / 1.25 / 600
- `heading-2` — 22px / 1.3 / 600
- `heading-3` — 18px / 1.35 / 600
- `heading-4` — 16px / 1.4 / 600
- `body-lg` — 16px / 1.6 / 400
- `body` — 14px / 1.6 / 400
- `body-sm` — 13px / 1.55 / 400
- `label` — 12px / 1.4 / 500 (used for badge text, field labels, metadata)
- `caption` — 11px / 1.4 / 400 (used for bubble attribution, API status label)

**Font recommendation**: A single variable font family with multiple weights covers both heading and body use. Recommended: **IBM Plex Sans** (heading weights), **IBM Plex Sans** (body weights) — a single family with clean humanist proportions, strong tabular figures for stat counters, and a deliberate product-software character. Fallback: system-ui. Do not mix two display families.

**Rules**:
- Line length in body text: max 65ch for reading comfort (applies to grammar explanations, memory card summaries).
- Do not use font-weight 700 (bold) anywhere in body text context. Reserve weight 600 for headings, 500 for labels, 400 for body.

---

### 5.2 Color System

**Base tokens** (define in CSS custom properties, all consumers reference tokens not raw values):

```
--color-canvas: #0D1117           /* Main page background */
--color-surface: #161B22          /* Card, panel backgrounds */
--color-surface-raised: #1C2128   /* Slightly elevated surfaces (drawer, active panels) */
--color-border: #30363D           /* Default border */
--color-border-subtle: #21262D    /* Subtle dividers */

--color-text-primary: #E6EDF3     /* Main text */
--color-text-secondary: #8B949E   /* Secondary labels, metadata */
--color-text-disabled: #484F58    /* Disabled text */

--color-primary: #58A6FF          /* Primary brand accent (blue) */
--color-primary-subtle: rgba(88, 166, 255, 0.12) /* Tinted backgrounds */
--color-primary-border: rgba(88, 166, 255, 0.4)  /* Selected card border */

--color-success: #3FB950          /* Proficient badge, correction text */
--color-success-subtle: rgba(63, 185, 80, 0.12)

--color-warning: #D29922          /* Grammar tip button, Needs Work badge */
--color-warning-subtle: rgba(210, 153, 34, 0.12)

--color-error: #F85149            /* Destructive buttons, muted state, error text */
--color-error-subtle: rgba(248, 81, 73, 0.12)

--color-tutor-bubble: #1C2128     /* Tutor message background */
--color-user-bubble: rgba(88, 166, 255, 0.15) /* User message background */
```

**Rules**:
- All colors reference tokens, never raw hex values in component code.
- Semantic tokens (e.g., `--color-success`) are used for status colors; never hardcode red or green.
- Color alone is never the only differentiator for state — always pair color with a shape or label change (accessible design).
- Light mode is not required for v1 but token naming should not assume dark mode (avoid `--color-dark-*` naming).

---

### 5.3 Spacing Scale

Base unit: **4px**. All spacing values are multiples of 4.

```
--space-1: 4px
--space-2: 8px
--space-3: 12px
--space-4: 16px
--space-5: 20px
--space-6: 24px
--space-8: 32px
--space-10: 40px
--space-12: 48px
--space-16: 64px
```

**Rules**:
- Internal card padding: `--space-5` (20px) default.
- Section vertical rhythm: `--space-12` (48px) between major sections.
- Component gap: `--space-4` (16px) in grids, `--space-3` (12px) in vertical stacks.
- Never use spacing values outside this scale in component code.

---

### 5.4 Border Radius

```
--radius-sm: 4px   /* Badges, chips, small pills */
--radius-md: 6px   /* Buttons, inputs, small cards */
--radius-lg: 8px   /* Cards, panels */
--radius-xl: 12px  /* Drawer, modals */
--radius-full: 9999px /* Circular elements (visualizer), round badges */
```

**Rules**: Do not use `border-radius: 50%` on non-square elements. Use `--radius-full` on pills only when the height is fixed. Cards use `--radius-lg`. The auth card uses `--radius-xl`.

---

### 5.5 Elevation / Shadow Philosophy

Shadows are used sparingly and only to communicate layer elevation, not decoration:

```
--shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.4)           /* Subtle card lift */
--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.5)          /* Drawer, modal */
--shadow-focus: 0 0 0 3px rgba(88, 166, 255, 0.3)   /* Keyboard focus ring */
```

**Rules**:
- Cards in the grid do not use shadows. Borders communicate containment.
- The progress drawer uses `--shadow-md` to visually separate from the lobby behind the overlay.
- Focus rings use `--shadow-focus` on all interactive elements (keyboard navigation).
- No inset shadows for decorative depth.

---

## 6. Interaction and UX Behavior

### 6.1 Hover / Focus / Active States

- **Hover**: Background lightens by one step (surface to surface-raised), or border becomes slightly more visible. Cursor changes to `pointer` on all interactive elements. Transition: 120ms ease-out.
- **Focus**: All focusable elements show a 3px focus ring using `--shadow-focus`. No outline suppression with `outline: none` without a replacement.
- **Active/Pressed**: Scale down to 0.97 over 60ms for buttons. Revert on release.
- **Selected** (scenario cards): Immediate border and background token switch on click. No animation required — the state must be instant.

### 6.2 Form Validation

- Validation runs on field blur (not on keystroke, not on submit only).
- Error text appears below the relevant field with the error color.
- On submit with invalid fields: focus moves to the first invalid field. Error states remain visible.
- On submit with valid fields: fields and button enter a disabled/loading state until the response resolves.
- Success on sign-in: show a brief success toast, then transition to the lobby (200ms delay to allow toast to register).
- Error on sign-in: show an error toast AND inline error on the password field ("Invalid credentials" — do not specify which field is wrong for security).

### 6.3 Session State Transitions

All session state changes (mute, pause, resume, complete) are immediate UI updates followed by async API calls. Do not wait for the API response to update the UI state — optimistic updates only. If the API call fails, roll back the state and show an error toast.

The visualizer state machine:
- `connecting` on session start, until WebRTC reports connected
- `active` when the tutor is speaking
- `listening` when the tutor finishes and awaits user input
- `muted` overrides all other states if the mic is muted

### 6.4 Subtitle Rendering

Subtitles appear as text appends (word by word) inside the subtitle overlay. The overlay fades in (opacity 0 to 1, 150ms) on the first word and fades out (150ms) when the tutor turn ends. Text is cleared after the fade-out completes (not before).

When the user begins speaking, the subtitle overlay must fade out within one animation frame — no 150ms delay. The clearing is immediate on the user's speech event.

### 6.5 Grammar Tip Expansion

The grammar expansion card renders below the user bubble via a CSS height animation (`max-height: 0` to `max-height: 300px`, 200ms ease-in-out). It does not push existing bubbles above it — it only pushes content below (normal document flow). The user bubble that triggered the expansion does not move.

### 6.6 Progress Drawer Auto-Sync

When triggered post-session completion, the drawer opens immediately and the refresh button enters its loading state automatically. The polling interval (3s, up to 18s total) runs in the background. The UI updates the drawer sections as each data response arrives. If polling completes without new data, the refresh button returns to its default state with no error notification — this is a background process, not a user-triggered one.

### 6.7 Confirmation Patterns

"Complete Session" does not require a confirmation dialog in the current spec. It is a clearly labeled destructive button. If added in a later iteration, the dialog should be a simple two-button modal (Confirm End, Cancel) — no checkbox "are you sure" patterns.

---

## 7. Responsive Strategy

### 7.1 Desktop (1024px and above)

Full experience as specified. Two-column session playground, three-column scenario grid, fixed 480px drawer.

### 7.2 Tablet (768px – 1023px)

- Lobby: Two-column scenario grid. Header collapses the user email, retains status badge and action buttons. "View Progress" becomes an icon-only button.
- Session playground: Columns stack. Left column (visualizer + controls) at 340px fixed height, full width. Right column (conversation log) fills remaining viewport height, scrollable.
- Drawer: slides in at full width (100%) with overlay behind it.

### 7.3 Mobile (below 768px)

- Auth: Card at 90% width, standard vertical padding.
- Lobby: Single-column scenario grid. Header retains only the API status badge and a hamburger/overflow menu for profile + logout + progress access (collapse low-priority items).
- Session playground: Left column collapses to a compact bar at the top (visualizer at 100px, status text, control buttons). Right column fills remaining screen. Subtitle overlay wraps to full width of the screen.
- Drawer: Full-screen panel (100vw x 100vh), slides up from bottom on mobile. Close button always visible.

### 7.4 Navigation Adaptation

There is no persistent navigation to collapse. The header actions (logout, progress, status badge) are the only elements that adapt. A mobile overflow menu (`...` or hamburger) containing logout and progress access is sufficient for tablet/mobile. The status badge remains always visible in the header — it is a live system indicator, not a navigation item.

---

## 8. Frontend Technical Recommendations

### 8.1 Framework and Component Architecture

**Recommended stack**: React with TypeScript. Component boundaries follow the screen/feature split defined in Section 2, with shared primitives in a common design system layer.

Folder structure:

```
src/
  components/
    ui/              # Primitive: Button, Input, Badge, Toast, Tabs, Card
    session/         # Visualizer, SubtitleOverlay, ConversationBubble, GrammarCard
    lobby/           # ScenarioCard, ScenarioGrid, SessionLaunchControl
    drawer/          # ProgressDrawer, VocabularyCloud, GrammarInsightCard, MemoryCard
    auth/            # AuthForm, AuthTabs
  screens/
    AuthScreen.tsx
    LobbyScreen.tsx
    SessionScreen.tsx
  hooks/
    useSessionState.ts
    useWebRTC.ts
    useProgressSync.ts
    useToast.ts
  tokens/
    colors.ts        # Exports CSS custom property references
    spacing.ts
    typography.ts
```

### 8.2 State Management

**Session state** (visualizer state, mute, pause, WebRTC status, subtitle stream, conversation log) belongs in a dedicated `useSessionState` hook or a lightweight context. Do not use global state (Redux, Zustand) for session state unless the session log needs to persist across navigation — it does not in the current spec.

**Progress data** (drawer content) is fetched and managed in `useProgressSync`, with local component state. It does not need to be globally accessible.

**Auth state** (user session token, email) is the only global state. This is managed in a top-level context or a small store (Zustand is appropriate here). Token persistence is localStorage, with the expiry check on app load described in the requirements.

**Toast state** is managed via a React context + `useToast` hook, rendering into a portal at the root level.

### 8.3 Animation Implementation

Use CSS custom properties and class-based state machines for the visualizer. Avoid JavaScript-driven animation loops (requestAnimationFrame) for the visualizer unless actual audio data integration is required. CSS `@keyframes` are sufficient for the four visual states defined.

Drawer slide-in: CSS `transform: translateX(100%)` to `translateX(0)` with `transition` on the drawer element. The overlay fades in/out with opacity. Use `visibility: hidden` alongside `opacity: 0` for proper accessibility (prevents focus trapping when the drawer is closed).

Grammar expansion: CSS `max-height` transition from `0` to a defined maximum. This requires a known max value — use `300px` as a safe ceiling.

### 8.4 Accessibility Baseline

- All interactive elements are keyboard navigable in logical order.
- All icon-only buttons have `aria-label`.
- The conversation log is an `aria-live="polite"` region so screen readers announce new messages.
- The subtitle overlay is an `aria-live="assertive"` region.
- The API status badge includes `role="status"`.
- Modals (if introduced) trap focus while open and restore focus on close.
- Color is never the sole differentiator for any state — always paired with a label or icon change.
- The application targets WCAG 2.1 AA contrast ratios. The color tokens defined in Section 5.2 meet this standard against `--color-canvas`.

### 8.5 Design System Approach

Build the UI primitives (Button, Input, Badge, Card, Toast) as a thin internal design system, not a dependency on a third-party component library. The visual requirements are specific enough that adapting a library (Shadcn, Radix + custom styles) is appropriate — use Radix UI primitives for accessibility behavior (dialog, tabs, dropdown) and apply the token-based styles on top. Do not use a component library's visual defaults.

---

## 9. Implementation Phasing

### Phase 1 — Foundation and Auth (Week 1)

- Token system setup (CSS custom properties, typography scale)
- Primitive component library: Button (all variants), Input, Tabs, Toast system
- Auth screen implementation (Sign In / Create Account tabs, form validation, loading states, toast integration)
- Route structure and auth context (token storage, expiry check, redirect logic)

Deliverable: Working auth flow with token persistence. Design tokens in place for all subsequent phases.

---

### Phase 2 — Lobby and Scenario Grid (Week 1–2)

- Header component (API status badge, user info, logout, progress button)
- Scenario card component with all states (default, hover, selected)
- Scenario grid with skeleton loading state
- Session launch control (disabled / active button)
- Integration: fetch scenarios from API, display connection status

Deliverable: Fully functional lobby. User can authenticate, view scenarios, select one, and trigger session start.

---

### Phase 3 — Session Playground — Static Layout (Week 2)

- Two-column session layout scaffold
- Visualizer component with all four CSS animation states (class-driven, no real audio yet)
- Subtitle overlay (show/hide, text streaming simulation)
- Control bar (mute, pause, complete) with all visual states
- Conversation log (tutor and user bubbles, scrolling behavior)
- Grammar tip badge and expansion drawer (toggle, animation)
- Live Sync badge

Deliverable: Full session UI rendered with static/mock data. All interactions functional at the UI level.

---

### Phase 4 — Session Playground — Live Integration (Week 3)

- WebRTC integration wired to visualizer state machine
- Subtitle streaming connected to real tutor output events
- Conversation log populated from live turn data
- Grammar tip content populated from grammar worker output
- Mute/pause/complete wired to backend API calls (optimistic updates + error rollback)
- Turn-cutoff logic (VAD-based delay handling) integrated

Deliverable: Fully live session with real audio and turn management.

---

### Phase 5 — Progress Drawer (Week 3)

- Drawer component (slide-in animation, overlay, close behavior)
- Stats row, vocabulary cloud, grammar insight cards, memory timeline
- Refresh/sync button with loading state
- Auto-sync polling logic post-session completion
- All empty states

Deliverable: Fully functional progress drawer with live data.

---

### Phase 6 — Responsive, Accessibility, and Polish (Week 4)

- Responsive behavior across tablet and mobile breakpoints
- Keyboard navigation audit across all screens
- `aria-live` region setup on conversation log and subtitles
- Focus management on drawer open/close, session start/end
- Contrast ratio audit against WCAG 2.1 AA
- Animation performance review (reduce motion support via `prefers-reduced-motion`)
- Error state edge cases: API offline banner, WebRTC connection failure messaging

Deliverable: Production-ready, accessible, responsive UI across all screens.

---

*End of document.*