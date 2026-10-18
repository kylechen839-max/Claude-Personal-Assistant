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
