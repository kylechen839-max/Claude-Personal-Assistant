# Design skills

Six vendored skills for app and website design work. Claude Code loads
every `.claude/skills/<name>/SKILL.md` in this repo, locally and in cloud
sessions, so any session working here can use them. The `designer`
agent (`.claude/agents/designer.md`) says when it loads each one.

| Skill | What it does | Source (pinned) | License |
|---|---|---|---|
| `frontend-design` | Makes UI with a deliberate visual direction instead of generic "AI" styling: typography, colour, layout, motion. | anthropics/skills @ dbd4588f9e1033efb41dad4bef2f7947c8993d44 | Apache-2.0 (`LICENSE.txt`) |
| `web-design-guidelines` | Reviews UI code against Vercel's Web Interface Guidelines (a11y, focus, forms, motion, typography, copy) and reports `file:line` findings. | vercel-labs/web-interface-guidelines @ 434b7f91364665f2f733b310ec54809bf8f37937 (rules vendored; wrapper rewritten to read them locally) | MIT (`LICENSE`) |
| `accessibility` | WCAG 2.2 audit and fixes, with ARIA and focus patterns. | addyosmani/web-quality-skills @ afa8da942115f2961fdbfa80807ea0b232ff6c00 | MIT (`LICENSE`) |
| `ui-ux-pro-max` | Reference data for palette, type and style decisions: 88 styles, 192 palettes as semantic roles, 74 font pairings, a 10-category UX rule set and a pre-delivery checklist. | nextlevelbuilder/ui-ux-pro-max-skill @ 28b3b8dee8c976df4fa3e94aed4e092ad43fcb03 (rule files and three data files vendored; search script dropped; Google Fonts columns stripped; wrapper rewritten) | MIT (`LICENSE`) |
| `taste-skill` | Anti-generic house rules, laws of type, space and colour, and a dated registry of generated-page tells. Checklist for site pages; tells list for app screens. | Leonxlnx/taste-skill @ 12af471f627f866411b4bd785b19646d3672784c (rules excerpted; `tells.md`, `colour.md`, `type.md` vendored, `type.md` §5 web-font recipe stripped; Node scripts and library dropped) | MIT (`LICENSE`) |
| `animation-vocabulary` | Names motion effects precisely and maps them to CSS transitions and WAAPI with `prefers-reduced-motion`; easing, duration and press-feedback standards for micro-interactions. | emilkowalski/skills @ a81dbca07f2217b7103e510c0f13f8d5b017afc9 (glossary from `animation-vocabulary`, `references/STANDARDS.md` from `review-animations`; Kwil section added) | MIT (`LICENSE`) |

To update one, download the new upstream files at a pinned commit, read
them in full, and commit them here with the new SHA in this table.
Last checked 2026-10-28: `web-design-guidelines` upstream HEAD is still
the pinned commit, and `frontend-design`'s files are unchanged at
anthropics/skills HEAD ca45fc4, so neither pin moved.

## Considered and not vendored

- **shadcn/ui** (shadcn-ui/ui, `skills/shadcn`, MIT, checked at
  96da9cc7f1aa10580a3741664e2a77c721206c00): needs React, Tailwind and
  the shadcn CLI. Its SKILL.md runs `npx shadcn@latest info` when it
  loads, pre-approves `npx`/`pnpm dlx`/`bunx` installs, and has the agent
  fetch docs from the web. Kwil has no React, Tailwind or build step. The
  stack-neutral part (semantic colour roles such as primary, on-primary,
  card, muted, border, ring) is already covered by `app.css` tokens and
  `ui-ux-pro-max`'s `colors.csv`.
- **GSAP skills** (greensock/gsap-skills, MIT, checked at
  aed9cfd3277740755f6bfc1155c7aa645403b760): API references for the GSAP
  library (tweens, timelines, ScrollTrigger, plugins, React and Vue
  hooks) that tell the agent to recommend GSAP. Kwil adds no animation
  library or CDN. The motion vocabulary comes from
  `animation-vocabulary` instead, mapped to CSS and WAAPI.
- **"TasteSkill"** is not the same as `frontend-design` (different
  author and repo, and a removal checklist rather than a direction), so
  it is vendored as `taste-skill`.

## Where this repo's rules win

The skills are general advice. When they disagree with `CLAUDE.md` or
`.claude/TEAM_MEMORY.md`, those win:

- **No CDNs or external assets.** Where a skill suggests a Google Font, a
  font CDN or `<link rel="preconnect">`, use system font stacks or fonts
  vendored into the repo. The web-font recipes were stripped from
  `taste-skill` (`type.md` §5) and `ui-ux-pro-max` (`typography.csv`);
  a pairing there is a mood to express with the system stack, not a
  download.
- **No frameworks or libraries.** No React, Tailwind, shadcn, GSAP,
  Framer Motion or Reanimated. Where a skill's example uses one, rebuild
  the idea in plain CSS and DOM code. Motion is CSS transitions,
  keyframes or the Web Animations API, always with a
  `prefers-reduced-motion` path.
- **CSP.** `kwil/webapp.py` serves `script-src 'self'` and
  `style-src 'self'`: no inline `<script>`, `<style>` or `style=""`
  attributes. Styles live in the stylesheets; scripts in their files.
- **Kwil's tokens decide.** Palettes, styles and type pairings from
  `ui-ux-pro-max` or `taste-skill` are proposals. Map them onto the
  tokens in `kwil/web/app.css`, keep both themes, and keep WCAG AA
  (`tests/test_theme.py`). Never paste raw hex into components. Kwil's
  blue accent is the brand's, whatever `taste-skill` says about palettes.
- **App screens keep their patterns.** `taste-skill`'s bans on hairlines,
  shadows and small labels are page rules. In the app, keep Kwil's
  existing rows, sheets, borders and buttons, and use only its tells
  list.
- **Decide, don't ask.** Where `frontend-design` says to confirm a
  direction with the client, pick one and say why in the PR; Kyle rolls
  back what he doesn't like.
- **Agent dashboard page:** no `innerHTML` (render with `textContent` and
  elements), no Map/Set `.add`/`.set`, desktop only. A skill's markup
  example is a pattern to rebuild with DOM calls, not code to paste.
- **No installs in cloud runs.** `accessibility` mentions `npx lighthouse`
  and `@axe-core/cli`; skip them unless Kyle asks. Check by reading code
  and with the repo's own tests.
- **No fetching rules at run time.** The skills read their local
  references only. Links in them (easing.dev, design references) are for
  a human to browse.
- **No bundled scripts.** None of the vendored skills ships a script.
  Where a reference mentions an upstream script (`search.py`,
  `draw.mjs`, `inspect.mjs`), use the local data and the designer's
  screenshots instead.
