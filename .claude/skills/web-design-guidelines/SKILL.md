---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Vercel's Web Interface Guidelines.

This copy is vendored: the rules live in `references/command.md` next to
this file (vercel-labs/web-interface-guidelines at commit
434b7f91364665f2f733b310ec54809bf8f37937, MIT, see `LICENSE`). The
upstream skill fetches the rules from the repo's `main` branch on every
run; this one reads the pinned local copy instead, so a change upstream
can't alter what the review does without a reviewed commit here.

## How it works

1. Read `references/command.md` (in this skill's folder).
2. Read the specified files (or pick the UI files the task is about).
3. Check them against every rule in `references/command.md`.
4. Output findings in its terse `file:line` format.

Rules that don't apply to the code at hand (React hydration, Next.js
`<Link>`, CDN preconnect, Tailwind class names) are skipped, not flagged.
See `../DESIGN_SKILLS.md` for where this repo's own rules win.
