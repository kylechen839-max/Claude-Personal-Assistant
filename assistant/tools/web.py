"""Internet access: Anthropic's server-side web search / web fetch tools,
plus a local tool that opens pages in the computer's default browser.

Web search and web fetch run on Anthropic's servers inside the same API
call (no code here executes them); their results come back as
`web_search_tool_result` / `web_fetch_tool_result` blocks. Opening a page
in the browser is an ordinary client tool run by `ToolRegistry`.
"""

from __future__ import annotations

import logging
import re
import webbrowser
from urllib.parse import quote_plus, urlparse

from assistant.tools import Tool

log = logging.getLogger(__name__)

# Models that support the dynamic-filtering versions (_20260209); older
# models (e.g. claude-sonnet-4-5, claude-haiku-4-5) get the basic versions.
_NEW_TOOL_MODELS = re.compile(
    r"claude-(opus-(4-[678]|5)|sonnet-(4-6|5)|fable|mythos)")


def tool_versions(model: str) -> tuple[str, str]:
    """(web_search type, web_fetch type) for a model."""
    if _NEW_TOOL_MODELS.search(model or ""):
        return "web_search_20260209", "web_fetch_20260209"
    return "web_search_20250305", "web_fetch_20250910"


def _domain_filters(cfg: dict) -> dict:
    # The API accepts allowed_domains or blocked_domains, never both.
    if cfg.get("allowed_domains"):
        return {"allowed_domains": list(cfg["allowed_domains"])}
    if cfg.get("blocked_domains"):
        return {"blocked_domains": list(cfg["blocked_domains"])}
    return {}


def server_tools(model: str, cfg: dict) -> list[dict]:
    """Tool definitions for web search / fetch, per the [web] config."""
    search_type, fetch_type = tool_versions(model)
    tools = []
    if cfg.get("search", True):
        search = {"type": search_type, "name": "web_search",
                  "max_uses": cfg.get("max_searches", 3), **_domain_filters(cfg)}
        loc = {k: v for k, v in (cfg.get("location") or {}).items() if v}
        if loc:
            search["user_location"] = {"type": "approximate", **loc}
        tools.append(search)
    if cfg.get("fetch", True):
        tools.append({"type": fetch_type, "name": "web_fetch",
                      "max_uses": cfg.get("max_fetches", 3),
                      "max_content_tokens": cfg.get("max_fetch_tokens", 8000),
                      **_domain_filters(cfg)})
    return tools


def open_in_browser(url: str | None = None, search: str | None = None,
                    opener=webbrowser.open) -> dict:
    """Open a web page (or a Google search) in the default browser."""
    if not url and not search:
        raise ValueError("Give a url or a search")
    if not url:
        url = "https://www.google.com/search?q=" + quote_plus(search)
    if "://" not in url:
        if re.match(r"(?i)(javascript|data|file|vbscript|about|blob):", url):
            raise ValueError(f"Only http(s) web addresses can be opened: {url}")
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Only http(s) web addresses can be opened: {url}")
    if not opener(url):
        return {"status": "failed",
                "message": "No web browser is available on this computer."}
    log.info("Opened %s in the browser", url)
    return {"status": "opened", "url": url}


def browser_tools() -> list[Tool]:
    return [Tool(
        "open_in_browser",
        "Open a web page on Kyle's computer in his default browser, e.g. "
        "'open YouTube', 'pull up the Canvas page', 'show me the recipe'. "
        "Give a full url, or a search to open Google results for it. Use "
        "this only when Kyle wants something shown on screen; to answer a "
        "question, use web search instead.",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "http(s) address to open"},
            "search": {"type": "string",
                       "description": "Google search to open if no url"}}},
        open_in_browser)]
