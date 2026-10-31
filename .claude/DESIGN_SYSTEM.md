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
  shadow. Only floating layers (sheets) get
  `--shadow-2`; the Settings page is a full-screen layer with no shadow.
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
- Desktop: the same bottom sheet over the whole window (rail included),
  at most 32rem wide. The one exception is the Ctrl+K switcher
  (`dialog.sheet.switcher`): near the top (12vh from 48rem), at most
  32rem, a field over its list, no grab handle.

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
- Empty state = one line; examples live in the footer ("No reminders
  yet." over a `.group-foot` that says what to say). A list inside a
  settings-style page (Memory's long-term notes) says it like a
  `.group-foot`: left-aligned, `--t-cap`, `--muted`.

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
- **The composer is one row** (redesign phase 4): `[+] [field] [mic, or
  Stop while busy] [Voice, or Send]` inside one `--surface` card with
  `--r-lg` corners and a `--line-strong` edge (the ring while focused).
  The buttons sit on the field's last line (`align-items: flex-end`). The
  field is a `<textarea rows="1">` that grows with `field-sizing: content`
  to about 6 lines, then scrolls; it never has its own border or ring.
  `app.js` `syncComposer()` decides what shows: Send (a 44px `--accent`
  circle with an arrow) only when there is something to send (words, files
  or an edit), otherwise Voice conversation (the same circle with the
  waveform mask); Stop (a 44px `--surface-2` circle with a filled square)
  replaces the mic while Kwil answers, and the mic comes back for a yes/no
  question. A control that hides while focused hands focus to the field.
  The mic is a quiet 44px line icon in `--text-2`, `--bad` with a ripple
  while recording; its level bar and hint show under the row only then.
- **Modes line** (`#composer-modes`) just above the row: the project pill,
  the study pill and the "Think harder is on" chip, all the study pill's
  shape (`--accent-soft`, `--accent-ink`, `--t-cap`, 44px tall). The line
  disappears when they are all hidden (`:has`). Nothing mode-like goes in
  the header any more.
- **Think harder and Study mode** are toggle rows in the "+" sheet, after
  the pickers and before Cancel (icon plus label; pressed, `--accent-ink`).
  Choosing one closes the sheet (`data-close`). At 4 files only the
  pickers are disabled, never "+". Holding Send still thinks harder (no
  callout: `-webkit-touch-callout: none`).
- **Keyboard:** `.app` takes `--app-h` (the visualViewport height, set by
  `app.js`), so the row sits on the iPhone keyboard; `html.kb-open` drops
  the safe-area padding under it. A swipe down closes the keyboard, except
  one that scrolls a long message inside the field.
- **Sources** under a reply: `--t-cap`, muted. Kwil's own sources are one
  wrapping line ("Sources" then items joined by a middle dot), each a
  plain accent-ink text button; web pages are rows of a small numbered
  `--surface-2` pill, the title in `--accent-ink` and the site muted. Every
  item is at least 44px tall; the superscripts in the text are
  `--accent-ink` at .7em and not tappable.

## Settings page
- The gear (`<details class="menu">`, `<summary aria-label="Settings">`:
  scripts and tests find it by that; on a phone the tab bar's Settings tab
  opens it and the gear is hidden) opens a full-screen page like iOS
  Settings: `position: fixed; inset: 0` on `--bg` with the four safe-area
  insets, a centred column of at most 40rem, no shadow, border or pop.
  Nothing scrolls behind it (`html:has(.menu[open])`); reduced motion drops
  its fade.
- A large title (`#settings-title`, `--t-title`, focused when it opens so
  VoiceOver starts there) and a plain **Done** (`#settings-done`, top
  right): Done and Esc close it and give focus back to the gear (`ui.js`),
  or on a phone to the tab bar's Settings tab. The tab bar stays over it
  (z-index 31 over the page's 30), and the page leaves room for it.
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

## Today screen (redesign phase 5)
- A full-screen view like History (`#today-view`, Back top left), opened
  by the header's 22px line calendar glyph (`#open-today`, before
  History), or on a phone by the tab bar's Today tab (the header button
  and Back are hidden there). Content is a centred column of at most 40rem.
- Sections are `.group-head` + `.group[role=list]`; each row sits in a
  `role=listitem` wrapper so a row that opens something stays a real
  `<button class="row">` with a `.row-chevron`. Title `--t-body`,
  subtitle `--t-cap` muted (an overdue one `--bad-ink`).
- The date headings ("Today", "Tomorrow", 700, with the date muted
  beside them) are the screen's one bold moment; the bar title is 500.
- "Ask Kwil" is a 44px `.btn.plain` at the row's right edge; such rows
  have no chevron.
- Empty or off: one calm muted line and at most one `.btn.plain` action
  ("Connect Google", "Reconnect Google", "Retry"). Sections with nothing
  to say (no brief, no research, no study items) are left out.
- A running research job shows a `.state` dot in `--accent-ink` that
  breathes (none with reduced motion); finished is `.state.on`.

## Tab bar (redesign phase 6)
- Below 48rem only: `<nav id="tabbar" class="tabbar" aria-label="Main
  navigation">` at body level, fixed to the bottom, four
  `button.tabbar-item`s in order Chat, Today, History, Settings: a 22px
  line glyph (`aria-hidden`; speech bubble, the calendar, the clock, the
  gear) over a `--t-cap` 500 label. `--tabbar-h` is 49px including the
  `--line` hairline on top; under it the home indicator's
  `env(safe-area-inset-bottom)`; the `.bar` blur background. 48rem and up
  it never shows: the desktop rail takes its place (below).
- Not tabs in the ARIA sense: no `role=tab`, no `.tabs` (that is the
  Chat/Lecture/Tutor segmented control). The current one is
  `aria-current="page"` (`--accent-ink`; the rest `--text-2`), focus is
  `--ring`.
- Root screens only (`app.js` `syncTabs`): the chat, its Settings page
  (Settings is current while the menu is open), Today and History. A
  screen reached with Back (transcript, Memory, Projects, a project,
  Reminders, the brief) and the sign-in have none. Hidden with the
  keyboard (`html.kb-open`) and in voice mode (`body.voice-open`).
- Each tab works through the header's own control (`#open-today`,
  `#open-history`, the menu), so Today and History show exactly when
  those would; on a phone those header buttons, the gear and the Back of
  Today and History are hidden (Back keeps its space, so the title stays
  centred). Chat never focuses the message field (no keyboard), and the
  current tab does nothing.
- While it shows, `.app`, `.history`, the Settings page and `.panel` leave
  `--tabbar-h` + the safe area at the bottom, and the composer drops its
  own safe-area gap.

## Desktop layout (48rem and up)
- The rail: `<nav id="rail" class="rail" aria-label="Main navigation">`,
  the body's first element, `--rail-w` (4.5rem) wide, a `--line` hairline
  on its right, on `--bg`. `button.rail-item`s, each a 22px glyph over a
  `--t-cap` label, at least 44px tall (56px): Search (its shortcut under
  it, "Ctrl K" or "⌘K", filled in by `ui.js`), Chat, Today, History, and
  Settings pinned to the bottom. The current one is `aria-current="page"`:
  `--accent-ink` on `--accent-soft`; hover is `--surface-2`; focus is
  `--ring`.
- It shows on every screen but the sign-in and voice mode (`app.js`
  `syncTabs`, `RAIL_FOR`): a transcript marks History; Memory, Projects,
  Reminders and the brief mark nothing. Like the tab bar, each item clicks
  the header's own control (Today and History show exactly when
  `#open-today`/`#open-history` would); the header's calendar, clock and
  gear are hidden while the rail shows, never removed.
- `body:has(#rail:not([hidden]))` is a grid: the rail in column 1, every
  `<main>` in row 1, column 2. The chat is one centred column of
  `--chat-w` (46rem): its parts get `--gutter` as inline padding (the log
  still scrolls from the window's edge). The Settings page starts at
  `--rail-w`; the rail (z-index 31) stays on top of it.
- 64rem and up, two panes (`body[data-split]`, set by `app.js` `show()`
  and redone on the media query's change): Today beside the chat
  (`minmax(20rem, 26rem)` on the right) and a transcript beside the
  History list (`minmax(18rem, 24rem)` on the left; the open row
  `aria-current="true"` on `--accent-soft`; the transcript's Back keeps its
  space but is hidden, Escape still goes back). Every other screen is one
  pane. A `--line` hairline between panes, never a shadow.
- The switcher (Ctrl+K, Cmd+K on a Mac; Search in the rail): a combobox
  (`#switcher-input`) over a listbox of `role=option` rows (44px,
  `aria-selected` on `--accent-soft`), a "Conversation" tag in `--muted`
  on past chats, "No matches" in a `role=status` line. Commands click the
  existing controls and show only while those do.
- Voice mode stays a full-window overlay: the page, rail and tab bar
  behind it are `inert`, focus starts on the orb, Escape ends it, focus
  returns to the Voice button (or the message field). Captions widen to
  40rem from 64rem.

## Every change
- Light and dark, phone (390x844) and desktop screenshots, English and
  Chinese (every new string in `i18n.js`); keep ids and hooks the scripts
  and tests rely on; bump `sw.js`'s cache when web assets change.
