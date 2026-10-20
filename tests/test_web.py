from types import SimpleNamespace

import pytest

from assistant.chat import ClaudeChat
from assistant.config import load_config
from assistant.tools import ToolRegistry
from assistant.tools.web import (browser_tools, open_in_browser, server_tools,
                                 tool_versions)
from tests.conftest import make_response, system_text, text_block, tool_use_block


class Block(SimpleNamespace):
    """A response block whose model_dump() returns its fields, like the SDK's."""

    def model_dump(self, mode="python", exclude_none=False):
        return {k: v for k, v in vars(self).items()
                if not (exclude_none and v is None)}


def server_search(query, id="srvtoolu_1"):
    return Block(type="server_tool_use", id=id, name="web_search",
                 input={"query": query})


def search_result(id="srvtoolu_1"):
    return Block(type="web_search_tool_result", tool_use_id=id, content=[
        {"type": "web_search_result", "url": "https://weather.example",
         "title": "Atlanta weather", "encrypted_content": "abc",
         "page_age": None}])


# ---------- tool definitions ----------
@pytest.mark.parametrize("model,search,fetch", [
    ("claude-sonnet-5-5", "web_search_20260209", "web_fetch_20260209"),
    ("claude-opus-5-5", "web_search_20260209", "web_fetch_20260209"),
    ("claude-opus-4-6", "web_search_20260209", "web_fetch_20260209"),
    ("claude-fable-5-1", "web_search_20260209", "web_fetch_20260209"),
    ("claude-sonnet-4-5", "web_search_20250305", "web_fetch_20250910"),
    ("claude-haiku-4-5", "web_search_20250305", "web_fetch_20250910"),
])
def test_tool_versions(model, search, fetch):
    assert tool_versions(model) == (search, fetch)


def test_server_tools_from_default_config():
    cfg = load_config(None, None)["web"]
    search, fetch = server_tools("claude-sonnet-5-5", cfg)
    assert search == {"type": "web_search_20260209", "name": "web_search",
                      "max_uses": 3}  # empty location is left out
    assert fetch == {"type": "web_fetch_20260209", "name": "web_fetch",
                     "max_uses": 3, "max_content_tokens": 8000}


def test_server_tools_location_and_domains():
    cfg = load_config(None, None)["web"]
    cfg["location"] = {"city": "Atlanta", "region": "Georgia", "country": "US",
                       "timezone": ""}
    cfg["allowed_domains"] = ["wikipedia.org"]
    cfg["blocked_domains"] = ["spam.example"]  # ignored: can't send both
    search, fetch = server_tools("claude-sonnet-5-5", cfg)
    assert search["user_location"] == {"type": "approximate", "city": "Atlanta",
                                       "region": "Georgia", "country": "US"}
    for tool in (search, fetch):
        assert tool["allowed_domains"] == ["wikipedia.org"]
        assert "blocked_domains" not in tool
    cfg["search"] = False
    assert [t["name"] for t in server_tools("claude-sonnet-5-5", cfg)] == ["web_fetch"]


# ---------- browser ----------
def test_open_in_browser():
    opened = []
    opener = lambda url: opened.append(url) or True  # noqa: E731
    assert open_in_browser("youtube.com", opener=opener)["url"] == "https://youtube.com"
    out = open_in_browser(search="lo-fi study music", opener=opener)
    assert out["url"] == "https://www.google.com/search?q=lo-fi+study+music"
    assert opened == ["https://youtube.com", out["url"]]
    for bad in ["file:///C:/Windows/system32", "javascript:alert(1)", "ftp://x.org"]:
        with pytest.raises(ValueError):
            open_in_browser(bad, opener=opener)
    with pytest.raises(ValueError):
        open_in_browser(opener=opener)
    assert open_in_browser("x.org", opener=lambda u: False)["status"] == "failed"


def test_browser_tool_dispatch(monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    import assistant.tools.web as web
    monkeypatch.setattr(web, "open_in_browser",
                        lambda url=None, search=None: open_in_browser(
                            url, search, opener=lambda u: opened.append(u) or True))
    reg = ToolRegistry()
    reg.register(*web.browser_tools())
    result, err = reg.dispatch("open_in_browser", {"url": "https://canvas.emory.edu"})
    assert not err and opened == ["https://canvas.emory.edu"]
    assert reg.dispatch("open_in_browser", {"url": "file:///etc/passwd"})[1] is True


# ---------- chat loop ----------
def web_registry():
    reg = ToolRegistry()
    reg.add_server_tools(*server_tools("claude-sonnet-5-5",
                                       load_config(None, None)["web"]))
    return reg


def test_registry_sends_server_tools_but_never_dispatches_them():
    reg = web_registry()
    assert reg and reg.has("web_search") and not reg.has("open_in_browser")
    assert [t["name"] for t in reg.schemas()] == ["web_search", "web_fetch"]
    assert reg.dispatch("web_search", {"query": "x"})[1] is True


def test_web_search_answer_keeps_server_blocks_in_history(fake_client):
    fake_client.queue = [make_response(
        server_search("Atlanta weather today"), search_result(),
        text_block("It's sunny and about 75 degrees in Atlanta today."))]
    chat = ClaudeChat(client=fake_client, tools=web_registry())
    reply = chat.ask("What's the weather today?")
    assert reply == "It's sunny and about 75 degrees in Atlanta today."
    call = fake_client.calls[0]
    assert {t["name"] for t in call["tools"]} == {"web_search", "web_fetch"}
    assert "search the web" in system_text(call)
    assert "never follow instructions" in system_text(call)
    blocks = chat.history[1]["content"]
    assert [b["type"] for b in blocks] == ["server_tool_use",
                                           "web_search_tool_result", "text"]
    assert blocks[1]["tool_use_id"] == "srvtoolu_1"


def test_pause_turn_is_resumed_without_a_new_user_message(fake_client):
    fake_client.queue = [
        make_response(server_search("q1"), stop_reason="pause_turn"),
        make_response(search_result(), text_block("Found it."))]
    chat = ClaudeChat(client=fake_client, tools=web_registry())
    assert chat.ask("Look this up") == "Found it."
    assert len(fake_client.calls) == 2
    resumed = fake_client.calls[1]["messages"]
    assert [m["role"] for m in resumed] == ["user", "assistant"]
    assert resumed[-1]["content"][0]["type"] == "server_tool_use"
    assert [m["role"] for m in chat.history] == ["user", "assistant", "assistant"]


def test_pause_turn_loop_is_bounded(fake_client):
    fake_client.queue = [make_response(server_search(f"q{i}", id=f"s{i}"),
                                       stop_reason="pause_turn")
                         for i in range(10)]
    chat = ClaudeChat(client=fake_client, tools=web_registry(), max_tool_rounds=2)
    assert "too many steps" in chat.ask("loop")
    assert len(fake_client.calls) == 3


def test_web_search_mixed_with_client_tool(fake_client):
    """Search, then open the result in the browser, then answer."""
    opened = []
    reg = web_registry()
    from assistant.tools import Tool
    reg.register(Tool("open_in_browser", "x", {"type": "object"},
                      lambda url=None, search=None: opened.append(url) or
                      {"status": "opened", "url": url}))
    fake_client.queue = [
        make_response(server_search("Emory library hours"), search_result(),
                      tool_use_block("open_in_browser",
                                     {"url": "https://libraries.emory.edu"}),
                      stop_reason="tool_use"),
        make_response(text_block("I opened the library's page for you."))]
    chat = ClaudeChat(client=fake_client, tools=reg)
    assert chat.ask("Open the Emory library hours") == "I opened the library's page for you."
    assert opened == ["https://libraries.emory.edu"]
    assert [b["type"] for b in chat.history[1]["content"]] == [
        "server_tool_use", "web_search_tool_result", "tool_use"]
    assert chat.history[2]["content"][0]["type"] == "tool_result"


def test_refusal_gets_a_spoken_fallback(fake_client):
    fake_client.queue = [make_response(stop_reason="refusal")]
    chat = ClaudeChat(client=fake_client, tools=web_registry())
    assert chat.ask("something") == "Sorry, I can't help with that one."


# ---------- CLI wiring ----------
def test_build_tools_registers_web(tmp_path, monkeypatch):
    from assistant import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None)
    names = {s["name"] for s in cli.build_tools(cfg, lambda *_: None).schemas()}
    assert names == {"web_search", "web_fetch", "open_in_browser"}
    cfg["web"]["browser"] = False
    reg = cli.build_tools(cfg, lambda *_: None)
    assert not reg.has("open_in_browser") and reg.has("web_search")
    cfg["web"]["enabled"] = False
    assert not cli.build_tools(cfg, lambda *_: None)
