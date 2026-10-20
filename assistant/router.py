"""Which model answers a turn: the fast one (Haiku) for short, simple
questions, the main one (Sonnet) for everything else.

Two layers, with no extra API call:
1. Before the request (choose_model): the main model for a conversation
   with untrusted text or an attachment in it, for long requests, and for
   words that usually mean a change (add, move, book, set, turn off, lock,
   pause, forget, save, ...), current information (search, weather, news)
   or reasoning (why, explain, plan). Words are a heuristic and can miss a
   phrasing.
2. After a fast-model response (needs_main_for): if it calls any tool not
   in READ_ONLY_TOOLS, that response is thrown away before anything runs
   or enters history, and the main model answers the turn
   (chat.ClaudeChat._run, logged as "escalated_tool"). So a change is
   always made by the main model, whatever the words were. Words the fast
   model already streamed before its tool call may have been shown or
   spoken.

Once a conversation needed the main model it stays there until it is reset
(chat.ClaudeChat.escalated): the prompt cache is per model, and the fast
model can't read the main model's thinking.

Confirmations don't depend on the model: the same tools, with the same
questions, run on either.

`[assistant] router = "off"` (or CLAUDE_ROUTER=off) sends every turn to the
main model.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

MAX_WORDS = 15
MAX_CHARS = 100
MAX_CJK_CHARS = 30  # Chinese has no spaces: count characters

# Tools the fast model may run: they only read. Any other tool call (every
# change, and any tool added later that isn't listed here) sends the turn to
# the main model. Web search and fetch run on Anthropic's side and are
# allowed on either model.
READ_ONLY_TOOLS = frozenset({
    "list_calendar_events",               # calendar.py
    "list_assignments",                   # notion.py
    "email_summary", "read_email",        # gmail.py
    "recall_notes", "list_recent_notes",  # notes.py
    "search_memory",                      # memory tools (cli.py)
    "review_memory",                      # memory_review.py
    "search_conversations",               # tools/history.py
    "check_dev_team",                     # devteam.py
    "ha_list", "ha_state",                # home_assistant.py
    "spotify_now_playing", "spotify_devices",  # spotify.py
})

# Words that send a turn to the main model. Word starts, so "reschedule"
# also matches "rescheduling"; a few extra matches ("planet") only cost a
# little, a missed one is caught by READ_ONLY_TOOLS.
_EN_KEYWORDS = (
    # changes to calendar, notes, email, Notion, music, the home, the PC
    r"create", r"add", r"schedul", r"move", r"reschedul", r"delet", r"cancel",
    r"remind", r"draft", r"e-?mail", r"repl(?:y|ies)", r"mark", r"status",
    r"assignment", r"notion", r"play", r"put", r"book", r"push", r"clear",
    r"set", r"lock", r"unlock", r"open", r"pause", r"resume", r"stop",
    r"skip", r"next", r"volume", r"dim", r"forget", r"remember", r"save",
    r"message", r"tell(?! me\b)", r"send", r"finish", r"done", r"change",
    r"update", r"(?:turn|switch)\b(?:\W+\w+){0,4}?\W+(?:on|off)\b",
    # current information
    r"search", r"look up", r"news", r"weather", r"price", r"latest", r"score",
    # reasoning and writing
    r"why", r"explain", r"compare", r"plan", r"help me write", r"code",
    r"calculat", r"translat",
)
_EN = re.compile(r"\b(?:" + "|".join(_EN_KEYWORDS) + ")", re.IGNORECASE)
_ZH_KEYWORDS = ("安排", "添加", "加", "删", "取消", "改", "邮件", "搜索",
                "天气", "新闻", "为什么", "解释", "翻译", "提醒", "记住", "打开",
                "关", "播放", "推", "设置")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def _needs_main(text: str) -> str | None:
    """A reason code if the words alone call for the main model."""
    text = text or ""
    cjk = len(_CJK.findall(text))
    if cjk:
        if len("".join(text.split())) > MAX_CJK_CHARS:
            return "long"
    elif len(text.split()) > MAX_WORDS or len(text) > MAX_CHARS:
        return "long"
    if _EN.search(text) or any(k in text for k in _ZH_KEYWORDS):
        return "keyword"
    return None


def needs_main_for(tool_names) -> bool:
    """True if a fast-model response calls a tool that isn't read-only."""
    return any(name not in READ_ONLY_TOOLS for name in tool_names)


def choose_model(chat, text: str, attachments=None) -> tuple[str, str]:
    """(model, reason code) for this turn of `chat` (a ClaudeChat). The
    reason is logged at INFO; the text never is."""
    from assistant.chat import has_attachments

    main, fast = chat.model, getattr(chat, "fast_model", None)
    if getattr(chat, "router", "off") == "off":
        reason = "off"
    elif not fast or fast == main:
        reason = "no_fast_model"
    elif attachments or has_attachments(chat.history):
        reason = "attachments"
    elif chat.tainted():
        reason = "untrusted"
    elif getattr(chat, "escalated", False):
        reason = "sticky"
    else:
        reason = _needs_main(text) or "simple"
    model = fast if reason == "simple" else main
    log.info("Route: %s (%s)", model, reason)
    return model, reason
