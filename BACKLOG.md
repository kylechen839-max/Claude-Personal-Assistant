# Backlog

Planned work that isn't built yet. Open items for Kyle (keys, sign-ins,
hardware) are in BLOCKERS.md.

## Before public release

### Hybrid model

Let more than one model provider answer, so the assistant keeps working (and
costs less) without depending on a single one.

- A provider layer, so more than one AI provider can answer.
- Automatic fallback when a provider is down or rate-limited.
- Simple requests go to a cheap or local model, while calendar and email
  actions stay on Claude.
- Check each provider's data-use terms, and name every provider in
  PRIVACY.md.

Haiku routing (`[assistant] router`, `assistant/router.py`) is the first
step: simple turns already go to Claude Haiku, and every tool call that
changes Kyle's things is made by the main Claude model (a Haiku answer that
tries one is dropped before it runs).
