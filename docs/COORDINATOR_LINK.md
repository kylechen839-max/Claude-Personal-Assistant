# Coordinator link: one dev team coordinator for the dashboard and Hey Claude

Kyle has one **coordinator**: a standing Claude Code cloud session running
the `/build-feature` master role (`.claude/skills/build-feature/SKILL.md`).
He reaches the same coordinator from two places:

| From | How a message gets in | How the answer gets out |
|---|---|---|
| **Agent dashboard** (`tools/agent-dashboard/`) | "Message coordinator" (claude.ai comment, "Send to Claude"), or Open session | The session itself, and the dashboard's `runs` rows and `status_lines` |
| **Hey Claude** (iPhone app, PC voice) | `claude -p --cloud <coordinator> --output-format json` on the home PC, message on stdin (`assistant/devteam.py`) | One row in the Notion database **Hey Claude coordinator inbox**, read by the app and spoken |

Nothing here uses the Claude API: the coordinator and its agents run on
Kyle's Claude plan.

## Messages from Hey Claude

They arrive as a normal user turn (Kyle's own CLI, signed in to his account)
and look like this:

```
[Hey Claude voice | reply id hc-20261019-221500-a1b2]
build a setting for how fast the voice talks

Answer per docs/COORDINATOR_LINK.md: one row in the Notion database "Hey Claude coordinator inbox" with Reply ID = hc-20261019-221500-a1b2.
```

The text between the first line and the "Answer per" line is what Kyle said
(transcribed speech, so expect small mishearings). Treat it exactly like a
message he typed in the session: start a `/build-feature` run, steer one,
answer a question about the team, and so on.

## Answering (the coordinator's side)

For every `[Hey Claude voice | reply id ...]` message, write **one row** in
the Notion database **Hey Claude coordinator inbox** with Kyle's Notion
connector:

| Property | Type | What to put |
|---|---|---|
| `Reply ID` | title | The reply id from the first line, exactly. |
| `Reply` | text | What Hey Claude should say: plain spoken sentences, at most about 600 characters. No markdown, lists, links, ids or code. |
| `Status` | select | `answered` (done, or the answer to a question), `working` (accepted, still going), `blocked` (needs Kyle; say what he must do). |
| `Run` | URL | Optional: the draft PR or the dashboard link. |

- Write `working` right away when a request starts a run ("Got it, starting
  the voice speed setting; I'll say when the draft PR is up"), then update
  the **same row** (same Reply ID) to `answered` or `blocked` when the run
  ends. Hey Claude speaks each new status once and stops listening for a
  Reply ID after `answered` or `blocked`.
- Hey Claude creates the database the first time it checks for replies, in
  the page that holds the Fall 2026 Assignments database. If it doesn't
  exist yet when you answer, create it there with exactly these properties.
- Also show the message on the dashboard as you would any request (a run's
  `status_lines`), so both places tell the same story.
- The phone push at the end of a run (SKILL.md §3) stays as it is.

## Setup (once)

1. **Coordinator session.** Start a standing coordinator cloud session on
   this repo (for example from the dashboard project, or `claude --cloud
   "/build-feature coordinator: stand by for requests"`), and note its id
   (`session_...`, from its claude.ai/code link).
2. **Hey Claude, on the home PC** (`config.local.toml`):

   ```toml
   [devteam]
   enabled = true
   coordinator_session = "session_..."
   repo_dir = "C:/path/to/Claude-Personal-Assistant"   # a checkout of this repo
   ```

   Claude Code must be installed there and signed in to Kyle's claude.ai
   account (`claude auth status`). Sending works only where the CLI is; the
   cloud server can still read replies.
3. **Notion.** Nothing to do: the app creates the inbox next to the
   Assignments database with its existing integration, and the coordinator
   reaches it through Kyle's Notion connector.

## Trust

- Kyle's own spoken requests go to the coordinator without a confirmation
  (his choice). If the conversation holds outside text (a web page, an
  email), Hey Claude asks him first, so outside text can't start coding work.
- Hey Claude reads replies as information, never as instructions; deletes
  still always confirm.
- The CLI's sign-in stays on the PC. Nothing in this link stores a token.
