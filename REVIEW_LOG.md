# Continuous code review log

Reviewer session on branch `claude/code-review`. Each cycle lists the commits
reviewed, new findings (sent to the owning session), and the status of
fixes that were assigned earlier.

## Cycle 1 — 2026-10-18 ~20:10-20:25 UTC

### Commits reviewed
| Branch | Head |
|---|---|
| master | 7031232 |
| phone-web | d9e416a |
| cloud-host | fbf255a |
| history | b188c7b |
| failover | 9f4f53c |
| features | ff0bffa |
| pi-preflight | 0ef8069 |
| voice-mode | merged into master |

### Tests
- **Results:** master 309 passed. features 384 passed (once Pillow is in the venv).
- **Other branches:** pass, except for environmental failures before eSpeak was installed: no eSpeak, and HF model downloads blocked by the proxy.
- **JS:** `node --test tests/js` in directory form fails on Node 22. With explicit files, it passes (12/12 on master, 33/33 on failover).
- **Skips:** no disabled tests found. The skips are only for missing keys, models or FTS5.

### New findings (sent to the owners)
**HIGH**
- master/voice-mode: a barge-in just before a tool call still runs the tool. A barge-in utterance can be taken as "yes" to a delete prompt the user never heard. Cause: `chat.py:238-244` doesn't check for cancel before dispatch, and `voice_session.py:387-397` `on_confirm` ignores `turn.stop`.

**MED**
- voice-mode: `voice.js:281-357`. Tapping End while the mic permission prompt is open leaves the mic and WebSocket running.
- voice-mode: STT backlog details. Transcription runs after cancel, the rate limit is per socket, and the queue is unbounded.
- features/master: Calendar and Notion read results aren't marked untrusted, so an injected invite can trigger create/move/update without confirmation.
- features: `webapp.py:502,571,596` logs email, draft and note content, because the web app now has the Gmail tools.
- features: Google `token.json` and `.oauth_state.json` are written world-readable (0644).
- cloud-host: the default trusted_proxies (all of RFC1918) lets the Docker gateway, LAN or Render private network peers forge X-Forwarded-For. That defeats the per-IP lockout and rate limit (PoC confirmed).
- cloud-host (plausible): behind Cloudflare on Render, the rightmost untrusted XFF hop may be a shared edge IP.
- failover: the cloud always thinks it's the leader (it can't reach tailnet health checks and has no peers), so scheduled jobs fire twice.
- failover: clock-skewed peers or expired tokens feed the global limiter and lock the owner out.
- history (plausible): resuming a conversation drops the untrusted (web content) flag.
- pi-preflight (plausible): replacing the legacy web task can leave port 8765 held and the phone app gone.

**LOW**
- cloud-host: the rate limiter dict has no real bound (IPv6 rotation). `DATA_DIR`/`HISTORY_PATH` are set but never read, so the docs are wrong.
- master: `is_cross_origin` still trusts X-Forwarded-Host from anyone. A malformed `interrupt` causes an unhandled exception. Transcripts are logged at INFO.
- failover: `dead_msgs` isn't scoped per conversation. The leader tie-break is skew-sensitive. The edge trusts X-Forwarded-Host.
- history: an orphan user row is left after an API failure. The background title call ends a resumed conversation.
- features:
  - "Add" on a lecture candidate is lost or duplicated.
  - Uploads are unbounded and never purged.
  - JPEG COM and PNG iCCP metadata survive.
  - The draft prompt shows only the Reply-To display name.
  - Unticking a scope breaks sign-in.
  - Windows `os.replace` races on job files.
- pi-preflight: legacy task detection isn't scoped to this repo. A failed `--help` probe silently drops the Web task. `smoke.py` sends the token over http to remote hosts.

### Integration
- **failover EventLog vs history HistoryStore:** HistoryStore implements none of since/append/log_id/by_conv/meta_get/meta_set, and the `append` signatures collide. Sent the full mismatch list to both owners:
  - no seq
  - rowids get reused
  - different id formats and regexes
  - ms vs s timestamps
  - source is NOT NULL
  - hard deletes come back from peers
- **history:** conflicts with current master in chat.py, cli.py, config.py and the docs.

### Fix verification (assigned 20:08; checked ~20:15)
| Item | Status |
|---|---|
| Voice delete-confirmation barge-in (HIGH) | PARTLY FIXED: barge-in during the question is handled; barge-in before confirm is not (see HIGH above) |
| Concurrent turns / confirmation binding | NOT FIXED (WebBackend watcher consumes shared events; `answer()` binds to `s.pending`) |
| STT backlog | NOT FIXED |
| Gmail compose scope / incremental scopes | NOT FIXED |
| Email in logs | PARTLY FIXED (CLI only; web app still logs) |
| Untrusted email → memory → actions | NOT FIXED for memory path |
| Duplicate lecture Notion pages | NOT FIXED |
| Global lockout (phone-web, cloud-host, failover) | NOT FIXED on all three |
| Cookie not revoked on logout | NOT FIXED |
| Slow-upload 503 DoS (cloud-host) | NOT FIXED |
| Deleted text in sync event log (failover) | NOT FIXED |
| Mic index 0 falsy (pi-preflight) | NOT FIXED (`preflight.py:478`, and master `cli.py:424`) |

Most fixes were assigned only minutes before this check, so I'll verify them again next cycle.

## Cycle 2: 2026-10-18 ~20:50-21:10 UTC

### Commits reviewed
| Branch | Range |
|---|---|
| master | 7031232..ab27a4f (voice-mode, phone-web and pi-preflight merges; memory tools; shared event log) |
| voice-mode | f111aff |
| phone-web | 7a48f9c (same as master) |
| pi-preflight | 0b6e4da |
| cloud-host | fbf255a..5afc4cb |
| failover | 9f4f53c..02eafbd |
| features | ff0bffa..e6a5a96 |
| history | b188c7b (unchanged) |

### Tests
| Branch | Result |
|---|---|
| master | 648 passed, 18 skipped |
| cloud-host | 741 passed |
| failover | 701 passed, node 33/33 |
| features | 671 passed |
| voice-mode | 329 passed (74c9e92) |

Possibly flaky, passing on rerun:
- `test_end_to_end.py:42` (15360 vs >16000 samples)
- `test_wakeword.py::test_pipeline_on_audio_file` (FileNotFoundError from the TTS fixture)

### New findings
**HIGH**
- master (pi-preflight `f618b83`): the auto-updater runs whatever is on `stable` with no signature, remote or CI check (`updater.py:190-212`). It runs pip install and `--check` from the new code. stable and master are unprotected, so anyone who can push gets code execution on the PC and Pi. The coordinator disabled updates. A fix is planned: a promotion workflow plus verify-commit, which I recommend making mandatory. Branch protection is a Kyle BLOCKER.

**MED**
- master/updater:
  - A tracked `config.toml` edit stalls updates for good.
  - Any HEAD move (a manual checkout) is treated as an update, and the rollback resets a feature branch.
  - pip runs into the venv while the children are still running, and rollback can't restore the old dependencies.
- master/phone-web: logout and logout-all don't close live voice WebSockets (`webapp.py:1146-1163`).
- features: the untrusted-invite check needs `organizer.self is False`, but the API omits `self`, so it never fires. The test fakes the field (`calendar.py:69`, `test_gmail.py:547`).
- features/master: the recalled-note untrusted mark is dead code (nothing writes `untrusted`). Memory can be poisoned through calendar content and then steer unconfirmed create/move/update.
- cloud-host: an unauthenticated attacker with 16 IPv6 /64s can fill all 32 slow-body slots, and the owner's correct requests then get 429 (simulated).
- failover:
  - The `winner()` window is anchored on the earliest observed fire, so it can elect nobody.
  - The branch is 41 commits behind master and still has the pre-fix login lockout (HIGH for merging).
- Integration cloud-host × failover (plausible, HIGH once merged): `EdgeMiddleware` has no `.state`, so CloudGuard rejects every cookie, and `hc1.` tokens are counted as wrong tokens.
- features: stale uploads are purged only at startup, and `UploadBusy` returns a 500.

**LOW**
- `answer()` is bound to `s.pending`, not to a specific confirmation.
- The 4-conversation cap can be exceeded by simultaneous voice handshakes.
- Tool inputs and memory queries are logged at INFO. `voice_session.py:449` still logs the summary on master; `f111aff` has the fix but isn't merged yet.
- `all_failures` has no cap.
- updater: the `--short=7` comparison, the lock stale time, and the idle-check gap.
- `Test-InRepo` substring match. smoke follows redirects with the token.
- `write_private` uses a PID-only temp name. A multi-address Reply-To shows an empty recipient.
- cloud-host: 10/8 still covers other Render services. Lockout cap eviction order.
- failover:
  - Redacted text stays in the WAL and freed pages.
  - A `delete_message` aimed at a tombstone makes the two stores diverge.
  - Leader and runner aren't wired up.

### Fix verification
| Item | Status |
|---|---|
| Barge-in before tool call (HIGH) | FIXED on master (`chat.py:241-251`, `voice_session.py:456-463`) |
| Concurrent turns / confirmation binding | MOSTLY FIXED (per-turn listener); `answer()` binding LOW remains |
| STT backlog | FIXED (per-session limit, 4 conv cap, 1+1 queue, stop before Whisper) |
| voice.js End during mic prompt | FIXED |
| `is_cross_origin` XFH, malformed interrupt | FIXED |
| Global lockout (phone-web / master) | FIXED, right token always accepted |
| Global lockout (cloud-host) | FIXED, except the slot-exhaustion 429 above |
| Global lockout (failover) | FIXED at the edge; branch webapp still old until master is merged |
| Cookie revoked on logout | FIXED (persisted allowlist + epoch), except voice WS stays open |
| Slow-upload DoS (cloud-host) | PARTLY: per-IP/global caps + deadlines, but global pool exhaustible from one /48 |
| cloud-host trusted proxies / IPv6 / caps / docs | FIXED (residual: 10/8) |
| Deleted text in sync log (failover) | FIXED (tombstone redaction, both orders); WAL residue LOW |
| failover leader / stale-token lockout / dead_msgs / XFH | FIXED (leader logic not yet wired) |
| Gmail send scope | PARTLY: code guard (`NoSendGmail`) + Gmail scopes only if enabled; token can still send (no narrower scope) |
| Email in logs | MOSTLY FIXED; `remember`/`search_memory` not private, TTS error log |
| Untrusted email → memory | PARTLY: user-only summaries; untrusted mark never written |
| Duplicate lecture pages | FIXED |
| Token files 0600 | FIXED (temp name race LOW) |
| features cycle-1 items #4-#9 | FIXED |
| Untrusted invites (features #3) | NOT EFFECTIVE (`organizer.self` omitted by API) |
| Mic index 0 | FIXED (`audio.resolve_mic_device`) |
| pi-preflight cycle-1 #1-#4 | FIXED (`72da662`) |
| history cycle-1 items | no new commits |

## Cycle 3 — 2026-10-19 ~01:25-01:40 UTC (60-min cadence, HIGH/MED only)

### Commits reviewed
- **master:** ab27a4f..aa990bd (71 files, about 10.7k lines). This merged:
  - pi-preflight (signed auto-updates, promote-stable)
  - phone-web (sign-out ends voice, confirmations bound by id)
  - cloud-host (Render, CloudGuard)
  - history (on the shared event log)
  - natural-voice (Kokoro, ElevenLabs, first-clause streaming)
- **cloud-host, history, natural-voice, pi-preflight:** fully merged, nothing ahead of master.
- **features 8d458cc:** not merged; reviewed on its branch.

### Tests
- **master aa990bd:** full suite green (`-x`, 0 failures).
- **Updater signature tests:** the real-signature tests are skipped here because ssh-keygen isn't installed. CI must run them.

### New findings
**HIGH (conditional)**
- master: promote-stable uses `STABLE_SIGNING_KEY` as a repository secret with no Environment, and the stable ruleset lets all of GitHub Actions bypass it. Any workflow pushed on any branch can sign and promote its own commit, so hosts with updates enabled would run it. Updates are off by default. Fix: an Environment limited to master with Kyle as reviewer, or Kyle's own key as the default.

**MED**
- master `updater.py:186-192`: OpenPGP/X.509 signatures are verified by gpg and bypass allowed_signers. Disable those programs or require an SSH signature.
- master `voice_ws.py:176-203`: a race lets a voice socket opened during logout-all survive it. Re-check `store.valid` after inserting into live.
- features `chat.py:181-188,283`: untrusted_memory resets every turn, so an injected instruction repeated by Claude runs unconfirmed in the next turn.
- features `chat.py:202-204`: a summary of a conversation steered by a recalled untrusted note is stored as trusted.
- features merge risk: chat.py and calendar.py were rewritten on both sides. The merge must keep untrusted_context, history-tool untrusted handling and the invite flag.
- master (until features merges): summaries are stored and recalled with no untrusted flag.

### Fix verification
| Item | Status |
|---|---|
| Updater runs unverified `stable` (HIGH, cycle 2) | FIXED: mandatory fail-closed verify (`updater.py:176-194`), allowed_signers outside checkout, updates off by default, CI-gated promote via workflow_run/push/master; see new conditional HIGH on the CI key |
| Updater config.toml stall / HEAD-move rollback / pip in live venv | FIXED |
| Logout closes voice WS | FIXED (`webapp.py:1230-1258`), race MED above |
| Confirmations bound by id; limiter bounded | FIXED |
| cloud-host login slot pool | FIXED: own pool (2/addr, 8 total, 2 s, /56); signed-in requests unaffected |
| cloud-host × failover EdgeMiddleware.state | PARTLY: build_app fails fast without WebAuth; hc1/sync routes pending failover merge |
| Container non-root / no secrets / 0.0.0.0 only in server profile | OK |
| History: resume untrusted flag | FIXED (`chat.py:194-195,224-225,258-259`) |
| History: auth/CSRF/delete confirm/FTS/XSS/purge (marker absent from db, -wal and -shm) | OK |
| Event log redaction left in WAL | FIXED (secure_delete + checkpoint TRUNCATE) |
| features organizer.self | FIXED on branch (flag unless `self is True`, plus eventType/guestsCanModify) |
| features untrusted note mark | PARTLY on branch (written and honored, but per-turn only; see MED) |
| natural-voice: key handling, opt-in cloud, pinned Kokoro hashes, confirm question unsplit | OK |

## Cycle 4: FINAL pre-release pass, 2026-10-19 ~02:30-02:50 UTC

### Commit reviewed
- **master 44b3aa6:** all branches merged, features included.
- **Not on master:** voice-mode f111aff. Only its test is missing; the code changes are already on master.

### Tests
- **Python:** 1411 passed, 48 skipped, 0 failed.
- **JS:** 45/45.

### Verdict: NO-GO for v1.0.0 until B1-B4 land. No HIGH findings.

### Blockers (MED on auth or privacy)
- **B1 (auth):** with failover on, "Sign out everywhere" doesn't revoke hc1 phone tokens (`webapp.py:1282-1294`; confirmed by a probe). The UI (`app.js:432`) and `PRIVACY.md:49` promise it does. Minimum fix is to change the wording; the full fix is a revocation epoch. Sent to failover.
- **B2 (privacy):** the voice CLI logs what you said and Claude's reply at INFO: `cli.py:369` and `:381`, plus ERROR at `:159`. This contradicts `PRIVACY.md:41`.
- **B3 (privacy):** tool input values are logged at INFO for non-private tools (`chat.py:467`, `tools/__init__.py:107`).
- **B4 (PRIVACY.md inaccurate):**
  - Windows token/credentials file privacy.
  - Quick notes are also in history and survive `delete_note`.
  - Lecture `notes.json`/`page.json` are kept indefinitely; failed jobs keep the transcript for 14 days.
  - "Checksum-verified" is true only for Kokoro.
  - Notion "note pages" don't exist.
  - Render log retention isn't mentioned.

### Non-blocking (functional, fail closed)
- Phone voice over hc1 is closed right after `auth_ok`: `WebAuth.signed_in` rejects hc1 sessions (`webapp.py:978`).
- Cookie sign-in on the cloud host with failover returns 401: the app origin is treated as cross-origin (`edge.py:208-211`).

### Verified
| Area | Result |
|---|---|
| Route auth (local, local+failover, cloud, cloud+failover) | OK. Every /api route returns 401 without credentials. hc1 scopes are separated (sync vs app). Minting is rate-limited per address, and the right token is never locked out. CloudGuard sees `app.state.web` through EdgeMiddleware. Bad hc1 tokens aren't counted as WEB_TOKEN failures. No tokens in URLs. Non-loopback bind requires WEB_TOKEN. |
| voice_ws logout race | FIXED (`voice_ws.py:203-206` re-check, watchdog `:233`) |
| Updater trust chain | FIXED. The CI key is in the `stable-signing` Environment (master only, reviewer required), and Kyle's own signed tag is the documented default. PGP/X.509 are rejected (`updater.py:194-195,206-212`). Fail-closed, off by default. |
| Gmail drafts-only | OK. `NoSendGmail` wraps both service builds, and there's no send path anywhere. |
| Untrusted memory end to end | OK. Traced with real code: invite → tainted → untrusted summary → recall → move asks and is refused. |
| Secrets in logs | OK (`RedactingFormatter`). |
| PRIVACY.md | 15 claims accurate. Inaccurate claims are listed under B4. |

## Cycle 4b: blocker re-check, 2026-10-19 ~02:45 UTC

- **Commit:** master fcdc8db.
- **Tests:** 1414 passed, 48 skipped.

| Blocker | Status |
|---|---|
| B2 | FIXED: `cli.py:159,370,383` log lengths only |
| B3 | FIXED: `chat.py:469` logs the name and argument keys; `tools/__init__.py:107` logs the name. A sweep of INFO/WARN/ERROR calls found no remaining text. |
| B4 | FIXED: every PRIVACY.md point is corrected |
| B1 | OPEN: PRIVACY.md wording is fixed, but the UI confirm (`app.js:432`) still promises "every phone" while failover hc1 tokens aren't revoked. Clears with the revocation epoch or corrected confirm text. |

Verdict: GO once B1 lands.

## Cycle 4c: release verdict, 2026-10-19 ~02:55 UTC

- **Commit:** master 8685701.
- **Tests:** 1422 passed, 48 skipped; JS 45/45.

### Verdict: GO for v1.0.0
| Item | Status |
|---|---|
| B1: failover sign-out everywhere | FIXED (`4e52277`). A synced not-before revocation in the event log. Probe on cloud plus failover: an hc1 token gets 200 on logout-all, then 401 on `/api/state`. Refresh keeps `signed_in`. |
| hc1 phone voice | FIXED (reaches `ready`) |
| Cloud plus failover cookie origin | FIXED (`/api/state` with the app Origin returns 200; cross-site POST returns 401) |
| B2, B3, B4 | FIXED (fcdc8db; voice-mode b97fac1 merged) |

### LOW, non-blocking
- With failover on and `[history]` disabled, there is no `sync_log`. The revocation not-before is then in memory only (`webapp.py:1003-1020`), so a restart forgets "sign out everywhere". Persist it locally, or require history when failover is on.

## Cycle 4d: release commit, 2026-10-19 ~03:10 UTC

- **Commit:** master 025d86d (v1.0.0 final; `stable` promoted).
- **Reviewed:** 8685701..025d86d, the features privacy merge. Clean.
  - `note_to_history` no longer stores the note text.
  - `notes.json` is deleted only after the Notion page is filed (`lecture.py:741-744`).
- **Tests:** 1422 passed, 48 skipped.
- **Verdict:** GO confirmed.
- **Next:** light checks every few hours, stopping if master doesn't change.

## Light check, 2026-10-19 ~05:50 UTC

- **Commits:** 025d86d..0c6ed55 (1.0.1).
- **Tests:** 1423 passed, 48 skipped.

The fix that persists failover "Sign out everywhere" in `web_sessions.json` (95dd77e) is verified. No HIGH or MED findings.

LOW (sent to failover): an unreadable sessions file resets `tokens_not_before` to 0, which re-admits revoked access tokens when history is off. Fix: set it to now on an unreadable file.
