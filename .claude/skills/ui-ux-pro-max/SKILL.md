---
name: ui-ux-pro-max
description: UI/UX design intelligence as local reference data - 88 UI styles, 192 product palettes with semantic token roles, 74 font pairings, and a 10-category UX rule set with a pre-delivery checklist. Use when choosing or checking a palette, type pairing or visual style for Kwil's web app or site, or when you want the full UX checklist for a review. Reference only; Kwil's own tokens win.
license: MIT (see LICENSE)
---

# UI/UX Pro Max (vendored reference data)

Vendored from nextlevelbuilder/ui-ux-pro-max-skill at commit
28b3b8dee8c976df4fa3e94aed4e092ad43fcb03 (path
`.claude/skills/ui-ux-pro-max`, MIT, see `LICENSE`). Only the reference
data and the rule files are vendored:

- `references/quick-reference.md`: all UX rules in 10 priority categories
  (accessibility, touch, performance, style, layout, type and colour,
  animation, forms, navigation, charts). Verbatim.
- `references/pro-rules.md`: polish rules for app UI and the canonical
  pre-delivery checklist. Verbatim.
- `data/styles.csv`: 88 named visual styles, each with keywords, colours,
  effects, best for, do-not-use-for, light/dark support, accessibility
  risk, CSS keywords and a checklist. Verbatim.
- `data/colors.csv`: 192 product-type palettes as semantic roles
  (Primary, On Primary, Secondary, Accent, Background, Foreground, Card,
  Muted, Border, Destructive, Ring). Verbatim.
- `data/typography.csv`: 74 heading/body pairings with mood and best-for
  notes. The upstream Google Fonts URL, `@import` and Tailwind columns
  were removed; Kwil loads no outside fonts.

Not vendored: the upstream `scripts/` (a BM25 search CLI whose
`--persist` mode writes `design-system/` files into the project), the
stack CSVs (React, Tailwind, shadcn and other stacks Kwil doesn't use),
Google Fonts and icon catalogues, GSAP presets, and the remaining data
files. Where the vendored rule files say "run `search.py`", "use
`--domain`" or "`--design-system`", search the CSVs below instead.

## Kwil comes first

Read `../DESIGN_SKILLS.md` ("Where this repo's rules win") and the
tokens at the top of `kwil/web/app.css` before using anything here.
This data proposes; Kwil's tokens decide.

- **Map, don't paste.** A palette row is a set of roles. Map a role
  onto an existing token (`--accent`, `--surface`, `--muted`, `--line`,
  `--bad`, ...) and keep Kwil's values unless the task is to change the
  palette. Never paste raw hex into components; never add a second
  palette.
- **Both themes, measured.** Any colour change gets a light and a dark
  value and must pass `tests/test_theme.py` (WCAG AA).
- **Fonts are a mood, not a download.** A pairing tells you the feel
  (humanist, geometric, editorial serif). Express it with the system
  stack in `--font` or a font vendored into the repo with its licence.
  No Google Fonts, no `<link rel="preconnect">`, no `@import`.
- **No frameworks.** Style rows mention Tailwind, MUI, React Native,
  Reanimated, GSAP or Framer Motion; translate the idea into plain CSS
  and DOM code under Kwil's CSP (no inline script or style).

## How to use it

1. **Name the decision.** Palette for a feature, a type pairing, a
   style direction, or a UX review.
2. **Search the data with Grep** on the keyword columns, then read the
   whole matching rows:
   - style: `Grep "calm|minimal|ios" data/styles.csv`. Check its
     *Do Not Use For*, *Accessibility* risk and *Status*
     (`deprecated` rows point at a replacement).
   - palette: `Grep "Chat|Voice|Calendar|Productivity" data/colors.csv`.
   - type: `Grep "readable|friendly|calm" data/typography.csv`.
3. **Check the fit** against Kwil: phone-first iOS PWA, calm and
   native-feeling, light and dark, English and Chinese strings.
4. **Say why** in one line in your report: the row(s) you used and how
   they map onto Kwil's tokens. If nothing fits, say so and fall back
   to Kwil's existing patterns; don't present a guess as a data match.

For a review, walk `references/quick-reference.md` §1-§3 (critical and
high) first, then the pre-delivery checklist in
`references/pro-rules.md`. Rules about native SDKs (accessibilityLabel,
dp units, haptics APIs) apply through their web equivalents.
