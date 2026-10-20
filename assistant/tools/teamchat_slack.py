"""Slack for the team chat tools (teamchat.py): the Web API with a user
token (SLACK_USER_TOKEN, xoxp-), read only.

User token scopes: channels:read channels:history groups:read
groups:history users:read files:read search:read. No im/mpim scopes, and
DMs found by search or files.list are dropped anyway. Keep the app
internal to the workspace: a distributed (non-Marketplace) app drops to
one history call a minute.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from assistant.tools.teamchat import (MAX_NAMES, MAX_TEXT, ChatError, ChatProvider,
                                      squash, unescape, utc_from_epoch)

log = logging.getLogger(__name__)

API = "https://slack.com/api/"
AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "token_expired",
               "account_inactive"}
SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic",
                 "channel_purpose", "channel_name", "group_join", "group_leave"}
MAX_LOOKUPS = 25  # user names looked up in one call
_MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL = re.compile(r"<#[CG][A-Z0-9]+\|([^>]*)>")
_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")
_SPECIAL = re.compile(r"<!(here|channel|everyone)[^>]*>")


class SlackChat(ChatProvider):
    service = "slack"
    token_env = "SLACK_USER_TOKEN"
    api_hosts = frozenset({"slack.com"})
    file_hosts = frozenset({"files.slack.com"})
    token_hosts = frozenset({"files.slack.com"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._names: dict[str, str] = {}

    def _call(self, method: str, **params) -> dict:
        resp = self._request("GET", API + method,
                             params={k: v for k, v in params.items() if v is not None})
        data = self._json(resp)
        if not isinstance(data, dict):
            raise ChatError("rejected", "not an object")
        if data.get("ok"):
            return data
        error = re.sub(r"[^a-z_]", "", str(data.get("error") or "unknown"))[:40]
        log.warning("Slack %s: %s", method, error)
        if error in AUTH_ERRORS:
            with self._lock:
                self._auth_failed = True
            raise ChatError("auth", error)
        if error == "missing_scope":
            needed = re.sub(r"[^a-z:,.]", "", str(data.get("needed") or ""))[:60]
            raise ChatError("missing_scope", error, needed=needed or "required")
        if error == "ratelimited":
            raise self._rate_limited(None)
        if error == "no_permission":  # this channel or file, not the token
            raise ChatError("no_access", error)
        if error == "not_in_channel":
            raise ChatError("not_in_channel", error)
        if error in ("channel_not_found", "thread_not_found", "file_not_found",
                     "file_deleted"):
            raise ChatError("not_found", error)
        raise ChatError("rejected", error)

    # ----- names and text -----
    def _name(self, user: str, budget: list[int]) -> str:
        if not user:
            return ""
        if user in self._names:
            return self._names[user]
        if budget[0] <= 0:
            return user
        budget[0] -= 1
        try:
            info = self._call("users.info", user=user).get("user") or {}
        except ChatError as e:
            if e.code in ("auth", "rate_limited"):
                raise
            budget[0] = 0  # e.g. no users:read: keep the ids
            return user
        profile = info.get("profile") or {}
        name = squash(profile.get("display_name") or info.get("real_name")
                      or info.get("name") or user, 60)
        if len(self._names) >= MAX_NAMES:
            self._names.clear()
        self._names[user] = name
        return name

    def _text(self, text: str, budget: list[int]) -> str:
        text = _MENTION.sub(lambda m: "@" + self._name(m.group(1), budget), text or "")
        text = _CHANNEL.sub(lambda m: "#" + m.group(1), text)
        text = _LINK.sub(lambda m: m.group(2) or m.group(1), text)
        text = _SPECIAL.sub(lambda m: "@" + m.group(1), text)
        return squash(unescape(text), MAX_TEXT)

    @staticmethod
    def _file(f: dict) -> dict:
        return {"id": str(f.get("id", "")), "name": squash(f.get("name") or f.get("title"), 120),
                "type": str(f.get("mimetype") or f.get("filetype") or "")[:60],
                "size": f.get("size")}

    def _message(self, m: dict, budget: list[int]) -> dict:
        author = (self._name(m.get("user", ""), budget) if m.get("user")
                  else squash(m.get("username") or (m.get("bot_profile") or {})
                              .get("name") or "bot", 60))
        out = {"id": str(m.get("ts", "")), "time": utc_from_epoch(m.get("ts")),
               "author": author, "text": self._text(m.get("text", ""), budget)}
        if m.get("reply_count"):
            out["thread"] = str(m.get("thread_ts") or m.get("ts"))
            out["replies"] = int(m.get("reply_count") or 0)
        files = [self._file(f) for f in m.get("files") or [] if isinstance(f, dict)]
        if files:
            out["files"] = files
        return out

    # ----- the provider interface -----
    def channels(self) -> list[dict]:
        found, cursor = [], None
        for _ in range(5):  # up to 1000 channels
            data = self._call("conversations.list", types="public_channel,private_channel",
                              exclude_archived="true", limit=200, cursor=cursor)
            for c in data.get("channels") or []:
                if c.get("is_im") or c.get("is_mpim"):
                    continue
                found.append({"id": str(c.get("id", "")),
                              "name": "#" + squash(c.get("name"), 80),
                              "member": bool(c.get("is_member"))})
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        log.info("Slack: %d channels", len(found))
        return found

    def read(self, channel: dict, thread: str | None, start: datetime | None,
             end: datetime | None, limit: int) -> tuple[list[dict], bool]:
        window = {}
        if start is not None:
            window = {"oldest": f"{start.timestamp():.6f}",
                      "latest": f"{end.timestamp():.6f}", "inclusive": "true"}
        if thread:
            data = self._call("conversations.replies", channel=channel["id"],
                              ts=thread, limit=limit, **window)
        else:
            data = self._call("conversations.history", channel=channel["id"],
                              limit=limit, **window)
        raw = [m for m in data.get("messages") or []
               if isinstance(m, dict) and m.get("subtype") not in SKIP_SUBTYPES]
        budget = [MAX_LOOKUPS]
        messages = [self._message(m, budget) for m in raw[:limit]]
        messages.sort(key=lambda m: float(m["id"] or 0))
        return messages, bool(data.get("has_more"))

    def search(self, query: str, channels: list[dict], start: datetime,
               limit: int) -> tuple[list[dict], str | None]:
        q = query
        if len(channels) == 1:
            q += f" in:{channels[0]['name']}"
        q += f" after:{(start.date() - timedelta(days=1)).isoformat()}"
        data = self._call("search.messages", query=q, count=min(limit, 100),
                          sort="timestamp", sort_dir="desc")
        budget = [MAX_LOOKUPS]
        found = []
        for m in (data.get("messages") or {}).get("matches") or []:
            ch = m.get("channel") or {}
            cid = str(ch.get("id", ""))
            when = utc_from_epoch(m.get("ts"))
            if (ch.get("is_im") or ch.get("is_mpim") or cid.startswith("D")
                    or when is None or when < start):
                continue  # never DMs
            item = self._message(m, budget)
            item["channel"] = "#" + squash(ch.get("name"), 80)
            item["channel_id"] = cid
            found.append(item)
        return found, None

    def files(self, channels: list[dict], start: datetime, limit: int,
              whole: bool = False) -> list[dict]:
        data = self._call("files.list", channel=None if whole else channels[0]["id"],
                          ts_from=str(int(start.timestamp())), count=min(limit, 100),
                          types="all")
        out = []
        for f in data.get("files") or []:
            item = self._file(f)
            item["time"] = utc_from_epoch(f.get("created") or f.get("timestamp"))
            item["channel_ids"] = [str(c) for c in
                                   (f.get("channels") or []) + (f.get("groups") or [])]
            out.append(item)
        return out

    def file_info(self, file_id: str) -> dict:
        if not re.fullmatch(r"F[A-Z0-9]{4,30}", file_id or ""):
            raise ChatError("not_found")
        f = self._call("files.info", file=file_id).get("file") or {}
        return {"name": f.get("name") or f.get("title") or "",
                "mimetype": f.get("mimetype") or "", "size": f.get("size"),
                "url": f.get("url_private_download") or f.get("url_private") or "",
                "channel_ids": [str(c) for c in
                                (f.get("channels") or []) + (f.get("groups") or [])]}
