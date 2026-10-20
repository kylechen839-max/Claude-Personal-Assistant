"""GitHub for the team chat tools (teamchat.py): issues, pull requests and
their comments over REST, Discussions over GraphQL (the only API that has
them), with a fine-grained read-only token (GITHUB_TOKEN).

A "channel" is a repository (owner/name), limited to `[teamchat]
github_repos` when that is set. A "thread" is an issue or pull request
number (12) or a discussion (D12). There are no shared files here.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from assistant.tools.teamchat import (MAX_TEXT, MAX_TITLE, ChatError, ChatProvider,
                                      squash, utc_from_iso)

log = logging.getLogger(__name__)

API = "https://api.github.com"
GRAPHQL = API + "/graphql"
MAX_DISCUSSIONS = 25
MAX_SEARCH_REPOS = 5
_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")

DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    discussions(first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { number title body updatedAt author { login } comments { totalCount } }
    }
  }
}"""
DISCUSSION_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    discussion(number: $number) {
      number title body createdAt author { login }
      comments(first: $first) { nodes { body createdAt author { login } } }
    }
  }
}"""


class GitHubChat(ChatProvider):
    service = "github"
    token_env = "GITHUB_TOKEN"
    api_hosts = frozenset({"api.github.com"})

    def __init__(self, *args, repos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.repos = [r.strip() for r in ([repos] if isinstance(repos, str)
                                          else (repos or []))
                      if _REPO.fullmatch(str(r).strip())]

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def _status_error(self, resp):
        headers = resp.headers or {}
        if resp.status_code in (403, 429) and (
                headers.get("Retry-After") or headers.get("x-ratelimit-remaining") == "0"):
            wait = headers.get("Retry-After")
            if wait is None:
                try:
                    wait = int(headers.get("x-ratelimit-reset")) - time.time()
                except (TypeError, ValueError):
                    wait = None
            return self._rate_limited(wait)
        return None

    def _get(self, path: str, params=None):
        return self._json(self._request("GET", API + path, params=params))

    def _graphql(self, query: str, **variables) -> dict:
        data = self._json(self._request("POST", GRAPHQL,
                                        body={"query": query, "variables": variables}))
        errors = (data or {}).get("errors") or []
        if errors:
            kinds = {str(e.get("type", "")) for e in errors if isinstance(e, dict)}
            log.warning("GitHub GraphQL: %s", ",".join(sorted(k[:30] for k in kinds)))
            if "NOT_FOUND" in kinds:
                raise ChatError("not_found")
            if "RATE_LIMITED" in kinds:
                raise self._rate_limited(60)
            raise ChatError("no_access" if "FORBIDDEN" in kinds else "rejected",
                            "GraphQL")
        return (data or {}).get("data") or {}

    @staticmethod
    def _login(item: dict) -> str:
        return squash(((item or {}).get("author") or (item or {}).get("user") or {})
                      .get("login") or "someone", 60)

    def _issue(self, it: dict) -> dict:
        return {"id": f"#{it.get('number')}", "thread": str(it.get("number", "")),
                "kind": "pull request" if it.get("pull_request") else "issue",
                "state": str(it.get("state", ""))[:20],
                "time": utc_from_iso(it.get("updated_at")), "author": self._login(it),
                "title": squash(it.get("title"), MAX_TITLE),
                "text": squash(it.get("body"), MAX_TEXT),
                "replies": int(it.get("comments") or 0)}

    # ----- the provider interface -----
    def channels(self) -> list[dict]:
        if self.repos:
            names = self.repos
        else:
            data = self._get("/user/repos", {"per_page": 100, "sort": "pushed"})
            names = [str(r.get("full_name", "")) for r in
                     (data if isinstance(data, list) else []) if isinstance(r, dict)]
        return [{"id": n, "name": n} for n in names if _REPO.fullmatch(n)]

    def read(self, channel: dict, thread: str | None, start: datetime | None,
             end: datetime | None, limit: int) -> tuple[list[dict], bool]:
        repo = channel["id"]
        if thread:
            t = str(thread).strip().lstrip("#")
            if re.fullmatch(r"[Dd]\d{1,9}", t):
                return self._discussion(repo, int(t[1:]), start, end, limit), False
            if not re.fullmatch(r"\d{1,9}", t):
                raise ChatError("not_found")
            return self._issue_thread(repo, t, start, end, limit)
        params = {"state": "all", "sort": "updated", "direction": "desc",
                  "per_page": min(limit, 100)}
        if start is not None:
            params["since"] = start.isoformat()
        data = self._get(f"/repos/{repo}/issues", params)
        raw = data if isinstance(data, list) else []
        items = [self._issue(it) for it in raw if isinstance(it, dict)]
        items += self._discussions(repo, start, limit)
        items = [i for i in items if i["time"] is not None
                 and (start is None or i["time"] >= start)
                 and (end is None or i["time"] < end)]
        items.sort(key=lambda i: i["time"])
        return items[-limit:], len(raw) >= params["per_page"]

    def _discussions(self, repo: str, start: datetime | None, limit: int) -> list[dict]:
        owner, name = repo.split("/", 1)
        try:
            data = self._graphql(DISCUSSIONS_QUERY, owner=owner, name=name,
                                 first=min(limit, MAX_DISCUSSIONS))
        except ChatError as e:
            if e.code in ("auth", "rate_limited", "down"):
                raise
            return []  # Discussions off, or the token can't read them
        nodes = (((data.get("repository") or {}).get("discussions") or {})
                 .get("nodes") or [])
        return [{"id": f"D{d.get('number')}", "thread": f"D{d.get('number')}",
                 "kind": "discussion", "time": utc_from_iso(d.get("updatedAt")),
                 "author": self._login(d), "title": squash(d.get("title"), MAX_TITLE),
                 "text": squash(d.get("body"), MAX_TEXT),
                 "replies": int((d.get("comments") or {}).get("totalCount") or 0)}
                for d in nodes if isinstance(d, dict)]

    def _issue_thread(self, repo: str, number: str, start, end, limit):
        head = self._get(f"/repos/{repo}/issues/{number}")
        params = {"per_page": min(limit, 100)}
        if start is not None:
            params["since"] = start.isoformat()
        data = self._get(f"/repos/{repo}/issues/{number}/comments", params)
        raw = data if isinstance(data, list) else []
        out = [self._issue(head)] if isinstance(head, dict) else []
        for c in raw:
            if not isinstance(c, dict):
                continue
            when = utc_from_iso(c.get("created_at"))
            if end is not None and when is not None and when >= end:
                continue
            out.append({"id": str(c.get("id", "")), "time": when,
                        "author": self._login(c), "text": squash(c.get("body"), MAX_TEXT)})
        return out[:limit], len(raw) >= params["per_page"]

    def _discussion(self, repo: str, number: int, start, end, limit) -> list[dict]:
        owner, name = repo.split("/", 1)
        data = self._graphql(DISCUSSION_QUERY, owner=owner, name=name,
                             number=number, first=min(limit, 100))
        d = (data.get("repository") or {}).get("discussion")
        if not isinstance(d, dict):
            raise ChatError("not_found")
        out = [{"id": f"D{d.get('number')}", "kind": "discussion",
                "time": utc_from_iso(d.get("createdAt")), "author": self._login(d),
                "title": squash(d.get("title"), MAX_TITLE),
                "text": squash(d.get("body"), MAX_TEXT)}]
        for c in (d.get("comments") or {}).get("nodes") or []:
            when = utc_from_iso((c or {}).get("createdAt"))
            if when is None or (start is not None and when < start) or (
                    end is not None and when >= end):
                continue
            out.append({"time": when, "author": self._login(c),
                        "text": squash(c.get("body"), MAX_TEXT)})
        return out

    def search(self, query: str, channels: list[dict], start: datetime,
               limit: int) -> tuple[list[dict], str | None]:
        repos = [c["id"] for c in channels][:MAX_SEARCH_REPOS]
        if not repos:
            return [], None  # never all of GitHub
        partial = (f"only the first {MAX_SEARCH_REPOS} repos were searched; name "
                   "a repo to search it" if len(channels) > MAX_SEARCH_REPOS else None)
        base = " ".join([query[:120]] + [f"repo:{r}" for r in repos]
                        + [f"updated:>={start.date().isoformat()}"])
        found, seen = [], set()
        # the issue search needs is:issue or is:pull-request in the query
        for kind in ("is:issue", "is:pull-request"):
            data = self._get("/search/issues", {"q": f"{base} {kind}", "sort": "updated",
                                                "order": "desc",
                                                "per_page": min(limit, 50)})
            for it in (data or {}).get("items") or []:
                if not isinstance(it, dict) or it.get("id", id(it)) in seen:
                    continue
                seen.add(it.get("id", id(it)))
                item = self._issue(it)
                repo = str(it.get("repository_url", "")).rpartition("/repos/")[2]
                item["channel"] = item["channel_id"] = repo
                found.append(item)
        return found, partial

    def files(self, channels, start, limit, whole=False):
        raise ChatError("no_files")

    def file_info(self, file_id: str) -> dict:
        raise ChatError("no_files")
