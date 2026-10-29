"""Memory tools.

`memory_tools(store)`: Kwil's own memory (memory_store.py), one store per
person: `remember` (when the user says "remember...", "always..." or "from
now on..."), `list_memories`, `update_memory` and `forget_memory` (asks
first). A memory saved or edited while untrusted text (a web page, an
email) is in the conversation asks first too and is stored marked
untrusted, so it is later shown as data, never as an instruction.

`search_memory_tool(memory)`: with `[memory] backend = "pinecone"`, a
search of the Pinecone notes and past conversation summaries on request
(relevant ones are already recalled before every reply)."""

from __future__ import annotations

from kwil.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted

SAVE_RULE = ("Don't save health, religion or politics unless the user asks "
             "you to remember that exact thing.")


def search_memory_tool(memory) -> Tool:
    def search_memory(query: str) -> dict:
        notes = memory.retrieve(query)
        if not notes:
            return {"notes": [], "message": "No matching notes in long-term memory."}
        return mark_untrusted({"notes": notes},
                              getattr(memory, "last_untrusted", False) is True)

    return Tool("search_memory",
                "Search the user's notes database (their project notes and "
                "past conversation summaries) and return the most relevant "
                "notes. Use when the user asks what their notes say about "
                "something, or asks you to look something up in them.",
                {"type": "object",
                 "properties": {"query": {"type": "string",
                                          "description": "What to look for."}},
                 "required": ["query"]},
                search_memory, private=True)


def _chat():
    """The conversation running this tool (chat.current_chat), or None."""
    from kwil.chat import current_chat

    return current_chat()


def memory_tools(store) -> list[Tool]:
    from kwil.memory_store import (MAX_MEMORIES, MAX_MEMORY_CHARS, MemoryFull,
                                   MemoryPaused, valid_id)

    def remember(note: str) -> dict:
        chat = _chat()
        if chat is not None and getattr(chat, "incognito", False) is True:
            return {"status": "incognito",
                    "message": "This conversation is incognito, so nothing is "
                               "saved. Say so in one short sentence."}
        try:
            saved = store.add(note, source=getattr(chat, "conversation_id", None),
                              untrusted=bool(UNTRUSTED_CONTEXT.get()))
        except MemoryPaused:
            return {"status": "paused",
                    "message": "Memory is paused in Settings, so nothing was "
                               "saved. Say so in one short sentence."}
        except MemoryFull:
            return {"status": "full",
                    "message": f"Memory is full ({MAX_MEMORIES} memories). Ask "
                               "the user which one to forget, or to tidy up in "
                               "Settings, Memory."}
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "saved", "id": saved["id"]}

    def list_memories() -> dict:
        memories = [{k: m[k] for k in ("id", "text", "created", "untrusted")}
                    for m in store.list()]
        return mark_untrusted({"memories": memories, "paused": store.paused},
                              any(m["untrusted"] for m in memories))

    def update_memory(memory_id: str, text: str) -> dict:
        old = store.get(memory_id)
        if old is None:
            return {"status": "error", "message": "No memory with that id."}
        try:
            # edited with outside text in context: untrusted from now on
            store.update(memory_id, text,
                         untrusted=old["untrusted"] or bool(UNTRUSTED_CONTEXT.get()))
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "updated", "id": memory_id}

    def forget_memory(memory_id: str) -> dict:
        if not store.delete(memory_id):
            return {"status": "error", "message": "No memory with that id."}
        return {"status": "forgotten", "id": memory_id}

    def short(text: str, n: int = 120) -> str:
        return text if len(text) <= n else text[: n - 3] + "..."

    def ask_forget(i: dict) -> str | None:
        memory_id = i.get("memory_id")
        old = store.get(memory_id) if valid_id(memory_id) else None
        # no such memory: nothing to ask; the tool says so
        return f"Forget this: {short(old['text'])}?" if old else None

    return [
        Tool("remember",
             "Save a short memory that will be loaded into every new "
             "conversation with the user. Use it when the user says "
             "'remember...', 'always...', 'from now on...' or 'next time...', "
             "or states a lasting preference. Write one self-contained "
             f"sentence in their words (at most {MAX_MEMORY_CHARS} characters). "
             + SAVE_RULE,
             {"type": "object",
              "properties": {"note": {"type": "string",
                                      "description": "The memory to save."}},
              "required": ["note"]},
             remember,
             # A web page or email must not be able to plant memories.
             confirm_if_untrusted=lambda i: f"Save this to memory: {short(str(i.get('note', '')), 200)}?",
             private=True),
        Tool("list_memories",
             "List everything saved in the user's memory, newest first, with "
             "ids for update_memory and forget_memory.",
             {"type": "object", "properties": {}},
             list_memories, private=True),
        Tool("update_memory",
             "Change the text of one saved memory (by id), e.g. when the user "
             "corrects or refines something they asked you to remember.",
             {"type": "object",
              "properties": {"memory_id": {"type": "string"},
                             "text": {"type": "string",
                                      "description": "The new memory text."}},
              "required": ["memory_id", "text"]},
             update_memory,
             confirm_if_untrusted=lambda i: f"Change this memory to: {short(str(i.get('text', '')), 200)}?",
             private=True),
        Tool("forget_memory",
             "Delete one saved memory by id, e.g. when the user says 'forget "
             "that...' or 'stop doing...'. The user is asked to confirm out "
             "loud automatically; just call this tool.",
             {"type": "object",
              "properties": {"memory_id": {"type": "string"}},
              "required": ["memory_id"]},
             forget_memory, confirm_prompt=ask_forget, private=True),
    ]
