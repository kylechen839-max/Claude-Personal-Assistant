"""Long-term memory tools: search the Pinecone notes on request, and save a
note when the user asks Claude to remember something.

Relevant notes are already retrieved automatically before every reply; these
tools let the user ask for it explicitly ("what do my notes say about...",
"remember that...")."""

from __future__ import annotations

from assistant.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted

MAX_NOTE_CHARS = 2000


def memory_tools(memory) -> list[Tool]:
    def search_memory(query: str) -> dict:
        notes = memory.retrieve(query)
        if not notes:
            return {"notes": [], "message": "No matching notes in long-term memory."}
        return mark_untrusted({"notes": notes},
                              getattr(memory, "last_untrusted", False) is True)

    def remember(note: str) -> dict:
        note = note.strip()[:MAX_NOTE_CHARS]
        if not note:
            return {"status": "error", "message": "Nothing to remember."}
        extra = {"kind": "note"}
        if UNTRUSTED_CONTEXT.get():  # saved with web/email text in context
            extra["untrusted"] = True
        memory.store(note, extra=extra)
        return {"status": "saved"}

    return [
        Tool("search_memory",
             "Search the user's long-term memory (their Pinecone notes and "
             "past conversation summaries) and return the most relevant notes. "
             "Use when the user asks what their notes or memory say about "
             "something, or asks you to look something up in their memory.",
             {"type": "object",
              "properties": {"query": {"type": "string",
                                       "description": "What to look for."}},
              "required": ["query"]},
             search_memory, private=True),
        Tool("remember",
             "Save a short note to the user's long-term memory. Use only when "
             "the user explicitly asks you to remember something; write the "
             "note as a self-contained sentence.",
             {"type": "object",
              "properties": {"note": {"type": "string",
                                      "description": "The fact to remember."}},
              "required": ["note"]},
             remember,
             # A web page or email must not be able to plant memories.
             confirm_if_untrusted=lambda i: f"Save this to memory: {i['note'][:200]}?",
             private=True),
    ]
