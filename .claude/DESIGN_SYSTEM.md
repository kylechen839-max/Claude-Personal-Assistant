# Kwil design system

The rules the designer agent enforces on `kwil/web/`. Approved by Kyle on
2026-10-28 (the "streamlined" proposal; mockups in the redesign lead's
`kit.css`). Calm, native-feeling on an iPhone, one blue accent. Tokens live
in `kwil/web/app.css` `:root`; `tests/test_theme.py` checks the contrast.
Plain CSS and HTML: no frameworks, no CDN, no inline styles or scripts (the
CSP forbids them).

## Tokens
- **Colour:** the existing `--bg`, `--surface`, `--surface-2`, `--text`,
  `--text-2`, `--muted`, `--line`, `--accent`, `--accent-ink`, `--ok`,
  `--bad-ink`, `--warn*` in light and dark, plus `--scrim` (a sheet's
  backdrop) and `--brand-*` / `--on-brand` (service tiles). Components
  never use a raw hex or rgb(): add a token. Text 4.5:1, icons and
  graphics 3:1.
- **Type** (17px base): `--t-title` 28 (large titles), `--t-head` 20
  (sheet titles), `--t-body` 17 (row titles, body), `--t-sub` 15
  (secondary lines, buttons), `--t-cap` 13 (subtitles, group headers,
  footers).
- **Spacing** (4px grid): `--s1` 4, `--s2` 8, `--s3` 12, `--s4` 16,
  `--s5` 24, `--s6` 32; page edge `--pad`.
- **Radii:** `--r-sm` 10, `--r-md` 14 (buttons, fields), `--r-lg` 20
  (cards, groups), `--r-pill`; sheets 22 at the top.
- **Sizes:** list rows `--row-h` 52px; every tap target at least 44px.
- **Depth:** a group is a tone step (`--surface` on `--bg`), never a soft
  shadow. Only floating layers (sheets, the menu popover) get
  `--shadow-2`.
- **Motion:** sheets slide in over `--t-sheet` 240ms on `--ease-sheet`
  `cubic-bezier(.32,.72,0,1)`; the scrim fades in over `--t-scrim` 200ms; a
  pressed row turns `--surface-2`; a pressed button scales to .97. Every
  animation has a `prefers-reduced-motion` path (no movement).
- **Focus:** `:focus-visible` shows `--ring` (`--ring-inset` inside a
  clipped group); never remove an outline without it.

## Buttons: one style per role
- `.btn.primary`: the one way forward on a screen (accent fill). At most
  one per view; once a service is connected its primary becomes secondary.
- `.btn.secondary`: other actions (surface fill).
- `.btn.plain`: a quiet text action ("Use an access token instead",
  "Set up").
- Destructive: red text (`.danger` / `.btn.destructive`), never a red fill
  except the final confirm of an irreversible delete.

## Lists: `.group > .row`
- Settings and lists are grouped cards: `.group-head` (13px, muted) above a
  `.group` (surface, `--r-lg`, `--shadow-1`), rows inside, hairline
  separators inset past the icon, an optional `.group-foot`.
- A row: 30px tile, a title (`--t-body`) with an optional subtitle
  (`--t-cap`, muted), then on the right a value, a switch, a status, or a
  plain action word; a chevron when the row opens something.
- The whole row is one real `<button>` (or a `<label>` for a switch).

## Status: `.state`
- A dot and one word, never a banner: `.state.on` (green: Connected),
  `.state.warn` (needs you: Sign-in expired, Key needed), `.state.off`
  (hollow dot: Not available).
- A row that is not set up shows the action instead ("Connect", accent).

## Sheets
- Details, forms, explanations and Disconnect open in a bottom sheet: a
  modal `<dialog>` (focus stays inside, Esc and a tap outside close it,
  focus returns to the row), on `--bg`, 22px top corners, a grab handle,
  a header (44px tile, `--t-head` title, Close), a one-paragraph
  explanation, then the controls. One sheet at a time.
- Desktop: the same sheet, centred, at most 32rem wide.

## Icons
- Service rows show the service's real mark from Simple Icons (CC0),
  vendored in `kwil/web/static/icons/` with its README (repo@SHA, licence,
  trademark note) and drawn from an inline `<symbol>` sprite: never a CDN or
  a runtime fetch.
- Every mark is white on a 30px tile of its brand colour (44px in a sheet
  header). A brand colour too light for a white mark at 3:1 is darkened only
  as far as needed; near-black tiles get a faint edge in dark mode. A
  service with no Simple Icons entry gets a letter tile.
- UI glyphs: 22px line icons, `stroke-width` 1.8, `currentColor`. No emoji.

## Empty states
- Say what will appear and how to get it, in one calm line, centred, muted;
  offer the one action that fills it (a `.btn.secondary` or `.btn.plain`).
  No illustrations, no exclamation marks.

## Bold is spent once
- Each screen gets one bold moment: the voice orb on the voice view, the
  dates on Today. Everything else stays regular or medium weight; titles
  use size, not weight, to lead.

## Chat screen (added in design pass 2, 2026-10-28)
- **The composer never leaves the screen.** Anything stacked above it (the
  first-run guide, the health banner, the queue) is `flex: none` with a
  capped height and scrolls inside itself; only the log flexes.
- **Queued messages** (typed while Kwil answers) are right-aligned bubbles
  on `--surface` with a dashed `--line-strong` edge, `--t-sub` text clamped
  to two lines, a `--t-cap` "Queued" under it and a 44px remove button. The
  queue holds at most about two bubbles of height before it scrolls.
- **Stop** sits in the composer, left of Send: the same 44px circle on
  `--surface-2` with a filled square glyph.
- **Think harder** sits left of Send (and Stop): a 44px circle with no
  fill and a 22px line bulb in `--text-2`; pressed (`aria-pressed="true"`)
  it takes `--surface-2` with the bulb in `--accent-ink`. It is a toggle
  for the next message only; holding Send does the same.
- **Sources** under a reply: `--t-cap`, muted. Kwil's own sources are one
  wrapping line ("Sources" then items joined by a middle dot), each a
  plain accent-ink text button; web pages are rows of a small numbered
  `--surface-2` pill, the title in `--accent-ink` and the site muted. Every
  item is at least 44px tall; the superscripts in the text are
  `--accent-ink` at .7em and not tappable.

## Settings page
- The gear (`<details class="menu">`, `<summary aria-label="Settings">`:
  scripts and tests find it by that) opens a full-screen page like iOS
  Settings: `position: fixed; inset: 0` on `--bg` with the four safe-area
  insets, a centred column of at most 40rem, no shadow, border or pop.
  Nothing scrolls behind it (`html:has(.menu[open])`); reduced motion drops
  its fade.
- A large title (`#settings-title`, `--t-title`, focused when it opens so
  VoiceOver starts there) and a plain **Done** (`#settings-done`, top
  right): Done and Esc close it and give focus back to the gear (`ui.js`).
- Sections in this order, each a `<div class="set-sec">` holding a
  `.group-head` over one `.group` of `.row`s: Voice, Memory and projects,
  Notifications, Connections, Privacy and data, People, Backends (its
  `<legend>` is the head), Your account. A status line under a card is a
  `.group-foot`. A section whose rows its scripts all hid disappears
  (`:has`), so a family member never sees an empty head. Never "Account"
  as a head (that string is the invite form's field in `i18n.js`).
- Rows inside a card are split by a hairline inset from the left; a row
  that opens another page (Memory, Projects, Reminders) ends in a
  `.row-chevron`; sign-out-everywhere and delete are `.row.danger` (red
  text, no fill). A person is a row: name (`--t-body`) over status
  (`--t-cap`), plain actions on the right ("New link" accent, "Remove"
  red); "Invite someone" is the card's last row.
- Group headers are sentence case at `--t-cap` everywhere, including
  `<legend>`s: never tracked capitals.

## Glyphs drawn in CSS
- A small glyph that depends on data (a conversation's source, the pin)
  is a CSS mask of an inline SVG data URI on `currentColor`, chosen by a
  data attribute (`.conv-icon[data-source=voice]`), never an emoji or a
  text symbol. Keep `role="img"` and the `aria-label` on the element.
- Words drawn with CSS `content` (the empty chat, "Summary") can't be
  reached by `i18n.js`: give each a `:root:lang(zh)` rule beside it.

## Every change
- Light and dark, phone (390x844) and desktop screenshots, English and
  Chinese (every new string in `i18n.js`); keep ids and hooks the scripts
  and tests rely on; bump `sw.js`'s cache when web assets change.
