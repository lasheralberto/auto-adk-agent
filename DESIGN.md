# Design System — Developer Productivity Tool

## Philosophy

**Clarity over decoration.** Every element earns its place by communicating something.
Remove all visual noise until only meaning remains. Then refine what's left until it feels inevitable.

Three principles drive every decision:
1. **Information over aesthetics** — density and legibility are features, not tradeoffs.
2. **Stillness by default** — animation and color are reserved for state changes, never decoration.
3. **Invisible chrome** — the interface disappears; the user's work is the protagonist.

---

## Foundations

### Grid & Spacing

Base unit: **4px**.
All spacing, sizing, and layout values are multiples of 4.

| Token       | Value | Usage                             |
|-------------|-------|-----------------------------------|
| `space-1`   | 4px   | Icon padding, tight gaps          |
| `space-2`   | 8px   | Inline element gaps               |
| `space-3`   | 12px  | Component internal padding        |
| `space-4`   | 16px  | Section padding, card padding     |
| `space-6`   | 24px  | Between related groups            |
| `space-8`   | 32px  | Between unrelated sections        |
| `space-12`  | 48px  | Page-level vertical rhythm        |

Never use arbitrary values like `13px`, `22px`, or `7px`. Odd multiples signal a system is breaking.

### Corner Radius

| Token        | Value | Usage                                          |
|--------------|-------|------------------------------------------------|
| `radius-sm`  | 4px   | Chips, badges, inline code snippets            |
| `radius-md`  | 8px   | Buttons, inputs, small cards                   |
| `radius-lg`  | 12px  | Panels, modal dialogs, larger cards            |
| `radius-xl`  | 16px  | Full-bleed section containers                  |
| `radius-full`| 9999px| Pills, avatar bubbles, toggle thumbs           |

**Rule:** Never mix `radius-md` and `radius-sm` in the same visual group. Components at the same hierarchy level share the same radius.

---

## Color

### Palette

All values are defined as CSS custom properties. Never hardcode hex values in component code.

```css
/* Backgrounds — from deepest to most elevated */
--color-bg-base:      #09111f;   /* page canvas */
--color-bg-raised:    #0e1a2e;   /* cards, panels */
--color-bg-overlay:   #152036;   /* dialogs, popovers */
--color-bg-subtle:    #1c2a42;   /* hover states, input fill */
--color-bg-muted:     #243352;   /* disabled surfaces */

/* Borders */
--color-border-default:  rgba(255,255,255,0.08);
--color-border-strong:   rgba(255,255,255,0.15);
--color-border-focus:    #4a80ff;

/* Text */
--color-text-primary:    #dae2fd;  /* body, headings */
--color-text-secondary:  #8a9bbf;  /* labels, metadata */
--color-text-tertiary:   #4e5f80;  /* placeholders, disabled */
--color-text-inverse:    #0b1326;  /* text on bright fills */

/* Brand / Interactive */
--color-accent:          #2665fd;  /* primary CTAs, active states only */
--color-accent-hover:    #3a74ff;  /* hover state of accent */
--color-accent-subtle:   rgba(38,101,253,0.12); /* tinted bg, selected rows */

/* Semantic */
--color-success:         #34c759;
--color-warning:         #ff9f0a;
--color-error:           #ff453a;
--color-error-subtle:    rgba(255,69,58,0.12);
```

### Usage Rules

- **Accent blue appears once per view**, on the highest-priority action. If two elements compete for blue, one is wrong.
- **Backgrounds never use pure black** (`#000`). The base is `#09111f` — a blue-black that reads as dark without flattening depth.
- **Text opacity must never substitute for semantic color tokens.** Use `--color-text-secondary`, not `color: white; opacity: 0.5`.
- **Never tint disabled states with opacity.** Use `--color-text-tertiary` and `--color-bg-muted` directly.

---

## Typography

### Typefaces

| Role          | Family              | Fallback          |
|---------------|---------------------|-------------------|
| UI / Body     | `"SF Pro Text"`     | system-ui, sans-serif |
| Display       | `"SF Pro Display"`  | system-ui, sans-serif |
| Monospace     | `"SF Mono"`         | ui-monospace, monospace |

**Fallback behavior:** On non-Apple platforms, `system-ui` maps to Segoe UI (Windows) and Roboto (Android/Linux). Acceptable degradation — the grid still holds.

### Scale

| Token           | Size  | Weight    | Line Height | Usage                       |
|-----------------|-------|-----------|-------------|-----------------------------|
| `text-display`  | 28px  | 700       | 1.2         | Feature titles, empty states|
| `text-title-1`  | 20px  | 600       | 1.3         | Page headings, dialog titles|
| `text-title-2`  | 17px  | 600       | 1.35        | Section headings, card titles|
| `text-body`     | 15px  | 400       | 1.5         | Primary content, descriptions|
| `text-callout`  | 14px  | 400       | 1.45        | Supporting content           |
| `text-footnote` | 13px  | 400       | 1.4         | Metadata, timestamps         |
| `text-caption`  | 12px  | 400       | 1.35        | Labels, breadcrumbs          |
| `text-mono`     | 13px  | 400       | 1.6         | Code, paths, IDs, hashes     |

### Rules

- **Letter spacing:** Display sizes (`text-display`, `text-title-1`) use `letter-spacing: -0.01em`. Body sizes use `0`. Never use `uppercase` with letter-spacing unless it's a monospace label.
- **Truncation:** Single-line truncation uses `text-overflow: ellipsis`. Multi-line uses `line-clamp: N`. Never let text wrap when the design assumes a fixed row height.
- **Numbers in tables:** Always `font-variant-numeric: tabular-nums` to keep columns aligned.
- **Code:** All file paths, keys, IDs, terminal output, and version strings use `text-mono`. Never render these in a proportional font.

---

## Components

### Buttons

Four variants. One surface. No exceptions for one-off styling.

| Variant     | Background             | Border                    | Text               | Usage                                   |
|-------------|------------------------|---------------------------|--------------------|-----------------------------------------|
| `primary`   | `--color-accent`       | none                      | `--color-text-inverse` | One per view — the primary action   |
| `secondary` | `--color-bg-subtle`    | `--color-border-default`  | `--color-text-primary`  | Supporting actions                 |
| `ghost`     | transparent            | none                      | `--color-accent`   | Inline, low-prominence links            |
| `danger`    | `--color-error-subtle` | transparent               | `--color-error`    | Destructive actions only                |

**Sizing:**

| Size   | Height | Padding H | Font size |
|--------|--------|-----------|-----------|
| `sm`   | 28px   | 12px      | 13px      |
| `md`   | 36px   | 16px      | 15px      |
| `lg`   | 44px   | 20px      | 17px      |

- Minimum tap target: **44×44pt** (applies even if the visible element is smaller — extend the hit area via padding).
- Loading state: replace label text with a spinner. Never disable the button without also communicating why.
- Icon-only buttons always have an accessible `aria-label`. The icon has `aria-hidden="true"`.

### Inputs

```
height: 36px              /* md size */
padding: 0 12px
border: 1px solid var(--color-border-default)
border-radius: var(--radius-md)
background: var(--color-bg-subtle)
color: var(--color-text-primary)
font-size: 15px
```

**States:**

| State    | Border                      | Background           |
|----------|-----------------------------|----------------------|
| Default  | `--color-border-default`    | `--color-bg-subtle`  |
| Hover    | `--color-border-strong`     | `--color-bg-subtle`  |
| Focus    | `--color-border-focus` 2px  | `--color-bg-base`    |
| Error    | `--color-error`             | `--color-error-subtle`|
| Disabled | `--color-border-default`    | `--color-bg-muted`   |

- **Never use `outline` as the focus indicator.** Use `border` state transition. This keeps the layout stable and looks correct on both light and dark backgrounds.
- Error messages live **below** the input, `8px` gap, `text-caption`, `--color-error`. Never inside a tooltip or popover.
- Placeholder text must be `--color-text-tertiary`. It is not a label substitute — every input has a real label.

### Cards

Cards have no elevation shadows. Depth is achieved through background color stepping and borders.

```
background: var(--color-bg-raised)
border: 1px solid var(--color-border-default)
border-radius: var(--radius-lg)
padding: var(--space-4)
```

**Interactive cards** (clickable, selectable) add:
```
cursor: pointer

:hover   → background: var(--color-bg-overlay)
:active  → background: var(--color-bg-subtle)
selected → border-color: var(--color-accent);
           background: var(--color-accent-subtle)
```

Never add `box-shadow` to indicate selected state — border color change is sufficient and doesn't affect layout.

### Chips / Badges

```
height: 22px
padding: 0 8px
border-radius: var(--radius-sm)
font-size: 12px
font-weight: 500
background: var(--color-bg-subtle)
color: var(--color-text-secondary)
border: 1px solid var(--color-border-default)
```

Semantic variants replace `--color-bg-subtle` and `--color-text-secondary` with their counterpart semantic tokens (success, warning, error).

### Dividers

```
border: none;
border-top: 1px solid var(--color-border-default);
```

Use dividers to separate **unrelated content groups**. Never use them between items in a homogeneous list — use spacing alone.

---

## Icons

- Library: **SF Symbols** (Apple platforms) / **Phosphor Icons** (cross-platform web).
- Default size: `16px`. Large context actions: `20px`. Navigation / hero use: `24px`. Nothing larger in UI.
- Icons are always `--color-text-secondary` unless they carry state (active = `--color-accent`, error = `--color-error`).
- Decorative icons use `aria-hidden="true"`. Meaningful icons have an `aria-label` on the parent element.
- Never scale icons with CSS `transform`. Use the icon at its intended size from the source.

---

## Motion

Motion communicates state, not personality.

| Use case                     | Duration | Easing                       |
|------------------------------|----------|------------------------------|
| Hover / focus feedback       | 80ms     | `linear`                     |
| Button press / toggle        | 120ms    | `ease-out`                   |
| Panel expand / collapse      | 220ms    | `cubic-bezier(0.4, 0, 0.2, 1)` |
| Modal enter / exit           | 280ms    | `cubic-bezier(0.4, 0, 0.2, 1)` |
| Toast / notification         | 300ms    | `ease-out` enter, `ease-in` exit |

**Rules:**
- Only animate `opacity`, `transform`, and `height`/`clip-path`. Never animate `color`, `border`, or `background-color` at durations longer than 120ms — it reads as sluggish.
- Respect `prefers-reduced-motion`. Any animation longer than 120ms must be skipped or replaced with a simple opacity fade.
- No looping animations in idle UI. Spinners and progress indicators are the only exception, and only when a process is actively running.

---

## Accessibility

These are requirements, not suggestions.

- **Contrast:** All text at `text-body` size or larger must meet **4.5:1** against its background. Text at `text-display` / `text-title-1` must meet **3:1**. Test with the actual rendered background, not an approximation.
- **Focus order:** Tab order follows visual reading order (top-left to bottom-right). Never use `tabindex > 0`.
- **Focus visibility:** Every interactive element must have a visible focus indicator. Our default is `border: 2px solid var(--color-border-focus)`.
- **Touch targets:** Minimum **44×44pt** on mobile. Extend hit area with invisible padding if needed.
- **Color alone never encodes meaning.** Status must be communicated with icon + color, or text + color. Never color alone.
- **ARIA roles:** Only use landmark roles (`main`, `nav`, `aside`, `dialog`) on elements that have no equivalent semantic HTML element. Don't add `role="button"` to a `<button>`.

---

## Do's and Don'ts

### Do
- Use `--color-accent` for **one element per view**, the most important user action.
- Keep all spacing on the **4px grid**. When in doubt, go up to the next multiple.
- Use **`text-mono`** for all technical strings: file paths, API keys, version numbers, commit hashes.
- Write labels in **sentence case**. Not Title Case. Not ALL CAPS except `text-caption` monospace labels.
- Use **border color transitions** (not shadows or size changes) to communicate hover and focus.

### Don't
- Don't use `opacity` to simulate disabled or secondary states. Use semantic color tokens.
- Don't stack more than **three background levels** in a single z-axis view. Base → raised → overlay is the limit.
- Don't place a `primary` button next to another `primary` button. One CTA per context.
- Don't animate `color` or `background-color` at durations over 120ms.
- Don't use border radius values not in the token set. No `border-radius: 6px` or `10px`.
- Don't use more than **two font weights** in a single component. `400` and `600` are the working pair.
- Don't add decorative dividers between items in a uniform list.