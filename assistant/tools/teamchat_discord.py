"""Discord for the team chat tools (teamchat.py): the REST API with a bot
token (DISCORD_BOT_TOKEN, "Authorization: Bot ..."), read only.

The bot needs View Channels and Read Message History (permissions=66560)
and the privileged Message Content Intent: without it Discord sends every
message with empty content and attachments, which is reported as
"intent_off" rather than "no messages". Attachments are downloaded from
Discord's CDN without the token (their URLs are signed).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from assistant.tools.teamchat import (MAX_TEXT, ChatError, ChatProvider, snowflake_time,
                                      squash, utc_from_iso)

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
USER_AGENT = ("DiscordBot (https://github.com/kylechen839-max/"
              "Claude-Personal-Assistant, 1)")
TEXT_CHANNELS = {0, 5}      # text and announcement channels
THREAD_TYPES = {10, 11, 12}  # announcement, public and private threads
MAX_GUILDS = 10
SEARCH_LIMIT = 25           # Discord's own maximum for guild search
MAX_SEARCH_GUILDS = 3       # servers searched in one call
MAX_SEARCH_CHANNELS = 20    # channels named in one server's search
_ID = re.compile(r"\d{5,25}")


def _blank(m: dict) -> bool:
    """A message with nothing in it: what Discord sends when the bot lacks
    the Message Content Intent."""
    return not any(m.get(k) for k in ("content", "attachments", "embeds",
                                      "sticker_items", "poll", "message_snapshots",
                                      "components"))


class DiscordChat(ChatProvider):
    service = "discord"
    token_env = "DISCORD_BOT_TOKEN"
    api_hosts = frozenset({"discord.com"})
    file_hosts = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
    token_hosts = frozenset()  # the CDN gets no token

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bot {self.token}", "User-Agent": USER_AGENT}

    def _status_error(self, resp):
        if resp.status_code == 429:
            try:
                body = resp.json() or {}
            except ValueError:
                body = {}
            return self._rate_limited(body.get("retry_after")
                                      or (resp.headers or {}).get("Retry-After"))
        return None

    def _get(self, path: str, params=None):
        return self._json(self._request("GET", API + path, params=params))

    def _obj(self, path: str) -> dict:
        data = self._get(path)
        if not isinstance(data, dict):
            raise ChatError("rejected", "not an object")
        return data

    # ----- messages -----
    @staticmethod
    def _attachments(m: dict) -> list[dict]:
        return [{"id": f"{m.get('channel_id')}:{m.get('id')}:{a.get('id')}",
                 "name": squash(a.get("filename"), 120),
                 "type": str(a.get("content_type") or "")[:60],
                 "size": a.get("size")}
                for a in m.get("attachments") or [] if isinstance(a, dict)]

    def _message(self, m: dict) -> dict:
        author = m.get("author") or {}
        out = {"id": str(m.get("id", "")), "time": utc_from_iso(m.get("timestamp")),
               "author": squash(author.get("global_name") or author.get("username")
                                or "someone", 60),
               "text": squash(m.get("content"), MAX_TEXT)}
        if author.get("bot"):
            out["bot"] = True
        thread = m.get("thread") or {}
        if thread.get("id"):
            out["thread"] = str(thread["id"])
            out["replies"] = int(thread.get("message_count") or 0)
        files = self._attachments(m)
        if files:
            out["files"] = files
        return out

    def _raw_messages(self, channel_id: str, start: datetime | None,
                      end: datetime | None, limit: int) -> tuple[list[dict], bool]:
        """Up to `limit` messages of one channel in [start, end), oldest
        first, and whether older ones in the window were left out."""
        params = {"limit": max(1, min(100, limit))}
        if end is not None:
            params["before"] = str(snowflake_time(end))
        raw = self._get(f"/channels/{channel_id}/messages", params)
        raw = [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []
        inside = [m for m in raw if start is None
                  or (utc_from_iso(m.get("timestamp")) or start) >= start]
        more = len(raw) >= params["limit"] and len(inside) == len(raw)
        humans = [m for m in inside if not (m.get("author") or {}).get("bot")
                  and m.get("type", 0) in (0, 19)]
        blank = [m for m in humans if _blank(m)]
        if blank and len(blank) * 2 > len(humans):
            log.warning("Discord: message text missing (Message Content Intent off)")
            raise ChatError("intent_off")
        inside.reverse()  # Discord sends newest first
        return inside, more

    # ----- the provider interface -----
    def channels(self) -> list[dict]:
        guilds = self._get("/users/@me/guilds", {"limit": 200})
        found = []
        for g in (guilds if isinstance(guilds, list) else [])[:MAX_GUILDS]:
            gid = str(g.get("id", ""))
            if not _ID.fullmatch(gid):
                continue
            chans = self._get(f"/guilds/{gid}/channels")
            for c in chans if isinstance(chans, list) else []:
                if isinstance(c, dict) and c.get("type") in TEXT_CHANNELS:
                    found.append({"id": str(c.get("id", "")),
                                  "name": "#" + squash(c.get("name"), 80),
                                  "server": squash(g.get("name"), 80), "guild_id": gid})
        log.info("Discord: %d channels", len(found))
        return found

    def _thread_of(self, channel: dict, thread: str) -> str:
        """The thread's id, once it is known to be in `channel`."""
        if not _ID.fullmatch(thread):
            raise ChatError("not_found")
        info = self._obj(f"/channels/{thread}")
        if (info.get("type") not in THREAD_TYPES
                or str(info.get("parent_id")) != str(channel["id"])):
            raise ChatError("not_found")  # never a channel off the allowlist
        return thread

    def read(self, channel: dict, thread: str | None, start: datetime | None,
             end: datetime | None, limit: int) -> tuple[list[dict], bool]:
        target = self._thread_of(channel, thread) if thread else channel["id"]
        raw, more = self._raw_messages(target, start, end, limit)
        return [self._message(m) for m in raw], more

    def search(self, query: str, channels: list[dict], start: datetime,
               limit: int) -> tuple[list[dict], str | None]:
        guilds: dict[str, list[dict]] = {}
        for c in channels:
            guilds.setdefault(c.get("guild_id", ""), []).append(c)
        names = {c["id"]: c["name"] for c in channels}
        cut = []  # every reason the search covered only part
        if len(guilds) > MAX_SEARCH_GUILDS:
            cut.append(f"only the first {MAX_SEARCH_GUILDS} servers were searched")
        found = []
        for gid, chans in list(guilds.items())[:MAX_SEARCH_GUILDS]:
            if not _ID.fullmatch(gid):
                continue
            if len(chans) > MAX_SEARCH_CHANNELS:
                reason = (f"only {MAX_SEARCH_CHANNELS} channels per server were "
                          "searched; name a channel to search it")
                if reason not in cut:
                    cut.append(reason)
            params = [("content", query), ("min_id", str(snowflake_time(start))),
                      ("limit", str(min(limit, SEARCH_LIMIT)))]
            params += [("channel_id", c["id"]) for c in chans[:MAX_SEARCH_CHANNELS]]
            resp = self._request("GET", f"{API}/guilds/{gid}/messages/search",
                                 params=params)
            data = self._json(resp)
            if resp.status_code == 202:  # {"code": 110000, "retry_after": 2}
                raise ChatError("search_indexing",
                                retry_after=(data or {}).get("retry_after") or 5)
            # a hit in a thread: its channel_id is the thread; the response
            # lists the threads with their parent channel
            parents = {str(t.get("id")): str(t.get("parent_id"))
                       for t in (data or {}).get("threads") or []
                       if isinstance(t, dict) and t.get("parent_id")}
            for group in (data or {}).get("messages") or []:
                hit = group[0] if isinstance(group, list) and group else group
                if not isinstance(hit, dict):
                    continue
                item = self._message(hit)
                where = str(hit.get("channel_id", ""))
                if where in parents:
                    item["thread"] = where
                    where = parents[where]  # the allowlist applies to the parent
                item["channel_id"] = where
                item["channel"] = names.get(where, "")
                found.append(item)
        return found, "; ".join(cut) or None

    def files(self, channels: list[dict], start: datetime, limit: int,
              whole: bool = False) -> list[dict]:
        if whole:
            raise ChatError("channel_needed")  # one request per channel: name one
        raw, _ = self._raw_messages(channels[0]["id"], start, None, 100)
        out = []
        for m in reversed(raw):  # newest first
            for f in self._attachments(m):
                f["time"] = utc_from_iso(m.get("timestamp"))
                f["channel_ids"] = [str(m.get("channel_id") or channels[0]["id"])]
                out.append(f)
        return out[:limit]

    def file_info(self, file_id: str) -> dict:
        parts = str(file_id or "").split(":")
        if len(parts) != 3 or not all(_ID.fullmatch(p) for p in parts):
            raise ChatError("not_found")
        channel_id, message_id, attachment_id = parts
        m = self._obj(f"/channels/{channel_id}/messages/{message_id}")
        a = next((a for a in m.get("attachments") or []
                  if str(a.get("id")) == attachment_id), None)
        if a is None:
            if _blank(m):
                raise ChatError("intent_off")
            raise ChatError("not_found")
        channel_ids = [channel_id]
        info = self._obj(f"/channels/{channel_id}")
        if info.get("type") in THREAD_TYPES and info.get("parent_id"):
            channel_ids.append(str(info["parent_id"]))  # a thread's file
        return {"name": a.get("filename") or "", "mimetype": a.get("content_type") or "",
                "size": a.get("size"), "url": a.get("url") or "",
                "channel_ids": channel_ids}
