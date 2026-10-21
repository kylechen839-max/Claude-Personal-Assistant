# Design skills

Three vendored skills for app and website design work. Claude Code loads
every `.claude/skills/<name>/SKILL.md` in this repo, locally and in cloud
sessions, so any session working here can use them.

| Skill | What it does | Source (pinned) | License |
|---|---|---|---|
| `frontend-design` | Makes UI with a deliberate visual direction instead of generic "AI" styling: typography, colour, layout, motion. | anthropics/skills @ dbd4588f9e1033efb41dad4bef2f7947c8993d44 | Apache-2.0 (`LICENSE.txt`) |
| `web-design-guidelines` | Reviews UI code against Vercel's Web Interface Guidelines (a11y, focus, forms, motion, typography, copy) and reports `file:line` findings. | vercel-labs/web-interface-guidelines @ 434b7f91364665f2f733b310ec54809bf8f37937 (rules vendored; wrapper rewritten to read them locally) | MIT (`LICENSE`) |
| `accessibility` | WCAG 2.2 audit and fixes, with ARIA and focus patterns. | addyosmani/web-quality-skills @ afa8da942115f2961fdbfa80807ea0b232ff6c00 | MIT (`LICENSE`) |

To update one, download the new upstream files at a pinned commit, read
them in full, and commit them here with the new SHA in this table.

## Where this repo's rules win

The skills are general advice. When they disagree with `CLAUDE.md` or
`.claude/TEAM_MEMORY.md`, those win:

- **No CDNs or external assets.** Where a skill suggests a Google Font, a
  font CDN or `<link rel="preconnect">`, use system font stacks or fonts
  vendored into the repo.
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
  references only.
