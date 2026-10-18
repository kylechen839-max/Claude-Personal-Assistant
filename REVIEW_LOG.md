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
