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
  `--bad-ink`, `--warn*` in light and dark. Never a raw hex in a rule
  except brand tiles (below). Text 4.5:1, icons and graphics 3:1.
- **Type** (17px base): `--t-title` 28 (large titles), `--t-head` 20
  (sheet titles), `--t-body` 17 (row titles, body), `--t-sub` 15
  (secondary lines, buttons), `--t-cap` 13 (subtitles, group headers,
  footers).
- **Spacing** (4px grid): `--s1` 4, `--s2` 8, `--s3` 12, `--s4` 16,
  `--s5` 24, `--s6` 32; page edge `--pad`.
- **Radii:** `--r-sm` 10, `--r-md` 14 (buttons, fields), `--r-lg` 20
  (cards, groups), `--r-pill`; sheets 22 at the top.
- **Sizes:** list rows `--row-h` 52px; every tap target at least 44px.

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

## Every change
- Light and dark, phone (390x844) and desktop screenshots, English and
  Chinese (every new string in `i18n.js`); keep ids and hooks the scripts
  and tests rely on; bump `sw.js`'s cache when web assets change.
