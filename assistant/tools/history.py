"""Conversation-history tools: Claude can find a past conversation and,
after a spoken yes, switch to it.

"Hey Claude, continue our conversation about the lab report" ->
search_conversations(query="lab report") -> resume_conversation(id) ->
"Continue 'Lab report error bars' from Tuesday?" -> "yes" -> the old
conversation is loaded and the talk goes on from there.

Search results hold titles, dates and short snippets only.
"""

from __future__ import annotations

from assistant import history
from assistant.chat import current_chat
from assistant.tools import Tool

MAX_RESULTS = 5


def _when(ts: float, tz=None) -> str:
    dt = history.local_dt(ts, tz)
    return f"{history.spoken_date(ts, tz=tz)} at {dt:%I:%M %p}".replace(" 0", " ")


def history_tools(store: history.HistoryStore, memory=None, tz=None) -> list[Tool]:
    def search_conversations(query: str = "", since: str = "", until: str = "",
                             source: str = "", pinned: bool | None = None) -> dict:
        chat = current_chat()
        current = chat.conversation_id if chat else None
        hits = history.search_conversations(
            store, query, memory, limit=MAX_RESULTS + 1,
            source=source or None, pinned=pinned,
            since=history.parse_day(since, tz=tz),
            until=history.parse_day(until, end=True, tz=tz))
        results = [{"id": h["id"],
                    "title": h["title"] or "(untitled)",
                    "when": _when(h["updated_at"], tz),
                    "where": history.SOURCE_LABELS.get(h["source"], h["source"]),
                    "snippet": history.plain_snippet(h.get("snippet", ""))}
                   for h in hits if h["id"] != current][:MAX_RESULTS]
        return {"conversations": results} if results else {
            "conversations": [], "message": "No saved conversations match."}

    def resume_conversation(conversation_id: str) -> dict:
        chat = current_chat()
        if chat is None or not chat.recorder or not chat.recorder.store:
            return {"status": "error", "message": "History is not available here."}
        if chat.incognito:
            return {"status": "error",
                    "message": "Incognito is on, so conversations can't be resumed."}
        conv = chat.request_resume(conversation_id)
        recent = [{"who": "Kyle" if m["role"] == "user" else "you",
                   "said": history._truncate(m["text"], 160)}
                  for m in conv["messages"] if m["kind"] == "chat"][-6:]
        return {"status": "resumed", "title": conv["title"],
                "when": _when(conv["updated_at"], tz), "summary": conv["summary"],
                "last_messages": recent,
                "message": "Switched to this conversation. Pick up where it "
                           "left off in one or two spoken sentences."}

    def confirm_resume(tool_input: dict) -> str:
        conv = store.get(tool_input.get("conversation_id", ""), messages=False)
        title = conv["title"] or "that conversation"
        return f"Continue '{title}' from {history.spoken_date(conv['updated_at'], tz=tz)}?"

    return [
        Tool("search_conversations",
             "Search Kyle's saved past conversations with you (spoken, typed, "
             "or on his phone). Use for 'what did we talk about yesterday', "
             "'find our chat about the lab report', or before resuming one. "
             "Give a few topic keywords as the query (leave it empty to list "
             "by date), and since/until dates for 'yesterday' or 'last week'. "
             "Returns titles, dates and short snippets.",
             {"type": "object", "properties": {
                 "query": {"type": "string",
                           "description": "topic keywords, e.g. 'lab report'"},
                 "since": {"type": "string",
                           "description": "YYYY-MM-DD, first day to include"},
                 "until": {"type": "string",
                           "description": "YYYY-MM-DD, last day to include"},
                 "source": {"type": "string", "enum": list(history.SOURCES),
                            "description": "wake = spoken to the PC/Pi, text = "
                                           "typed, web = phone app, voice = "
                                           "phone voice mode"},
                 "pinned": {"type": "boolean",
                            "description": "only pinned conversations"}}},
             search_conversations),
        Tool("resume_conversation",
             "Switch to a saved conversation so Kyle can continue it ('continue "
             "our conversation about X'). Use an id from search_conversations; "
             "if several match, ask which one first. Kyle is asked to confirm.",
             {"type": "object", "properties": {
                 "conversation_id": {"type": "string"}},
              "required": ["conversation_id"]},
             resume_conversation, confirm_prompt=confirm_resume),
    ]
