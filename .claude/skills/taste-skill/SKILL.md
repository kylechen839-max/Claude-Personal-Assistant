---
name: taste-skill
description: Anti-generic ("anti-slop") rules for web pages - the house rules that strip templated AI styling (eyebrow labels, gradients, glows, icon-card rows, violet or cream-and-terracotta palettes), the laws of type, space and colour, and a dated registry of generated-page tells. Use as a checklist when building or reviewing Kwil's public site pages, and as a tells list for app screens. Pairs with frontend-design.
license: MIT (see LICENSE)
---

# Taste Skill (adapted)

Adapted from Leonxlnx/taste-skill at commit
12af471f627f866411b4bd785b19646d3672784c (`skills/taste-skill`, upstream
name `design-taste-frontend`, MIT, see `LICENSE`). The house rules and
laws below are excerpted from its `SKILL.md`; `references/tells.md`
and `references/colour.md` are verbatim, and `references/type.md` is
verbatim except §5, whose web-font `<link>` and `preconnect` recipe is
replaced by a note.

Not vendored: the Node scripts (`draw.mjs`, `inspect.mjs`, `lint.mjs`,
`scenes.mjs`, and `assets.mjs`, which searches and downloads pictures
from the web), the `library/` data they read, the bundled CSS/JS assets,
and the marketing-page run built on them. Where a vendored reference
mentions those scripts, the direction sheet, `--keep`, `--allow` or
`.taste-skill/`, skip that step: the designer's Playwright screenshots
and the repo's tests are the check here.

## How it differs from `frontend-design`

`frontend-design` sets a direction. This skill is a list of what to
remove and why: the devices that make a page read as generated, each
with what to do instead. Load `frontend-design` for the direction, then
use this as the checklist.

## Where it applies in Kwil

- **Public site pages** (`kwil/web/landing.html`, `waitlist.html`,
  `privacy.html`, `terms.html`, `site.css`): the house rules and laws
  apply as written, except where Kwil's rules win (below).
- **App screens** (`kwil/web/index.html`, `app.css`, sheets, rows,
  settings): the upstream skill's own app chapter allows quiet 1px
  borders, small labels and repeated panels there. Use only the tells
  registry (`references/tells.md`) and the colour and type laws, and
  keep Kwil's existing row, sheet and button patterns.
- **Kwil's rules win** (`../DESIGN_SKILLS.md`): no web fonts or CDNs
  (use the system stack in `--font` or a vendored font), no new
  libraries, CSP-safe markup, tokens from `app.css`, and WCAG AA in both
  themes. Kwil's accent (`--accent`, a calm blue) is the brand's and
  stays.

## House rules (quoted, lightly trimmed)

| Rule | Instead |
| --- | --- |
| **Headlines: two lines or fewer, at every width.** Every `h1`–`h6`, and the main line of the first screen whatever its tag. | Cut words. Then widen the measure. Size last. |
| **A clear title on the first screen**, in the page's one `h1`, and a heading on every section. | Say what this is: the name with what it does, the product with what it is for, the event with its date and place. |
| **Equal outer margins; no section huddled on one side** with the other half of the screen empty. | Close every void with something on its far side: the section's picture, a second column, a list, a figure. |
| **No blank stretches** taller than about half a screen. | The normal section gap. A deliberate rest is a placed picture or a field of colour, once per page. |
| **Statements are paragraphs.** An intro sentence or a quotation set large is marked up as `<p>` or `<blockquote>`, stays at statement size (36px or less on desktop, 24px on a phone) and runs to about 25 words, three lines. | Larger than that it is a headline and the two-line rule applies. |
| **No eyebrows.** No small label above a heading, no pill badge above the headline, no "New ·" chip. | Put the information in the heading or the first sentence, or drop it. |
| **No monospace** outside real code blocks. | The text face. Tabular figures where numbers align. |
| **No index numbering.** No 01 / 02 / 03, no (001), no "Step 2". | Order shown by position, size, or the words. A real `<ol>` if order matters. |
| **No thin rules.** No hairline dividers, no 1px outlined boxes, no grid lines, crosshairs or crop marks. | Space. A step in background tone. A change of scale. A filled shape. |
| **No gradients.** Not as backgrounds, not on buttons, not in text. | Flat colour, or a real photograph. A neutral scrim under text on a photo is the one exception. |
| **No shadows on things at rest.** No glows. | A tone step between surfaces. Shadows only on what truly floats: a menu, a dialog. |
| **No assistant palette.** No yellow-cream ground with terracotta or rust; no violet or indigo. | The dealt palette. |
| **No tiny uppercase letter-spaced labels.** | Sentence case at a readable size. |
| **No icon-topped feature cards, no rows of identical cards.** | The dealt section blueprint. |
| **No glass.** No backdrop blur panels, no floating pill navigation. | A plain bar: wordmark, a few links, one action. |
| **No emoji, no gradient blobs, no decorative shapes, no fake dashboards of grey bars.** | A real picture, a real interface fragment, or nothing. |
| **No styled accent word** in a headline (italic, serif, coloured, underlined). | One face, one colour. The words carry the emphasis. |
| **Two typefaces at most,** usually one. | The dealt type system. |

"The dealt palette / type system / section blueprint" means Kwil's own
tokens and patterns here; there is no draw script. Three places keep
their own conventions: real code in `<pre>`/`<code>`, real tables and
form fields, and a real interface fragment of the app shown on the site.

Removing ornament leaves space. Never fill it with another ornament.
Fill it with scale (make the main thing larger), with the full content,
with a real picture, or with a field of flat colour. Leave it empty on
purpose once per page at most.

## Laws of the craft (excerpted)

**Type**
- Two or three sizes do the visible work: one very large, one small,
  nothing in between. Five sizes on a page is the ceiling.
- Display type: tracking -0.02 to -0.05em, leading 0.85–1.05. Text:
  16–20px, leading 1.45–1.6, 45–75 characters per line.
- Left-aligned by default. Centre at most one moment.

**Space**
- One grid, one pair of tight margins (16–32px), used everywhere. Text
  blocks share two or three left edges on the whole page.
- The gap between sections is clearly larger than any gap inside one.
- Each section has a different composition from its neighbours.

**Colour**
- One ground, one ink, one accent. The accent appears on the action and
  perhaps one other thing.
- Running text in the full ink. A page set in soft grey reads
  unfinished.
- Surfaces are separated by a small step in tone, never by a line or a
  shadow.

**Components**
- Buttons: flat, one fill colour, either square-cornered or fully round,
  decided once.
- Navigation: wordmark, three to five plain links, one action.
- A control explains how to use it by its form, its label and its place.
  A limit that remains (password rules, file type and size) stays
  visible beside the field and is tied to it with `aria-describedby`.

**Copy.** No invented proof (customers, quotes, reviews, logos,
statistics). Labels name the action, not "Get started" or "Learn more".

## References

| File | Read it when |
| --- | --- |
| `references/tells.md` | Reviewing any page or screen: the dated registry of generated-page tells and what replaces each. |
| `references/colour.md` | Applying colour roles, proportions, tone steps, dark pages, contrast. |
| `references/type.md` | Setting a type scale, display and text settings, one family or two. |
