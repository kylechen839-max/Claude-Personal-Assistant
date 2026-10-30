"""Memory tools.

`memory_tools(store)`: Kwil's own memory (memory_store.py), one store per
person: `remember` (when the user says "remember...", "always..." or "from
now on..."), `list_memories`, `update_memory` and `forget_memory` (asks
first). A memory saved or edited while untrusted text (a web page, an
email) is in the conversation asks first too and is stored marked
untrusted, so it is later shown as data, never as an instruction.

In a conversation that is in a project (projects.py), `remember` saves to
that project unless the person says it's general (`general: true`), and
list / update / forget see the global memories and that project's only:
another project's memory is "no memory with that id" there, and outside
any project every project memory is.

`search_memory_tool(memory)`: with `[memory] backend = "pinecone"`, a
search of the Pinecone notes and past conversation summaries on request
(relevant ones are already recalled before every reply)."""

from __future__ import annotations

from kwil.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted

SAVE_RULE = ("Don't save health, religion or politics unless the user asks "
             "you to remember that exact thing.")


def search_memory_tool(memory) -> Tool:
    def search_memory(query: str) -> dict:
        notes = memory.retrieve(query, project=_project(_chat()))
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


def _incognito(chat) -> dict | None:
    if chat is not None and getattr(chat, "incognito", False) is True:
        return {"status": "incognito",
                "message": "This conversation is incognito, so nothing is "
                           "saved. Say so in one short sentence."}
    return None


def _from_outside(chat) -> bool:
    """Save what is written now as untrusted? Only when outside text (web,
    email, an attachment...) is in this conversation: a conversation
    tainted only by a loaded untrusted memory still asks first
    (UNTRUSTED_CONTEXT), but what the person confirms there is theirs.
    Without a running chat, whatever made the call untrusted counts."""
    if not UNTRUSTED_CONTEXT.get():
        return False
    outside = getattr(chat, "outside_content", None)
    return bool(outside()) if callable(outside) else True


def _project(chat) -> str | None:
    """The project the running conversation is in (ClaudeChat.project_id)."""
    project = getattr(chat, "project_id", None) if chat is not None else None
    return project if isinstance(project, str) else None


def memory_tools(store) -> list[Tool]:
    from kwil.memory_store import (MAX_MEMORIES, MAX_MEMORY_CHARS, MemoryFull,
                                   MemoryPaused, NoSuchProject, valid_id)

    def visible(memory: dict | None) -> dict | None:
        """The memory, if this conversation may see it: a global one, or
        one of its own project's (never another project's)."""
        if memory is None or memory.get("project") in (None, _project(_chat())):
            return memory
        return None

    def remember(note: str, general: bool = False) -> dict:
        chat = _chat()
        refused = _incognito(chat)
        if refused:
            return refused
        project = None if general is True else _project(chat)
        try:
            saved = store.add(note, source=getattr(chat, "conversation_id", None),
                              untrusted=_from_outside(chat),
                              **({"project": project} if project else {}))
        except NoSuchProject:
            return {"status": "error",
                    "message": "This conversation's project was deleted, so "
                               "nothing was saved. Ask whether to remember it in "
                               "general instead (general: true)."}
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
        if project:
            return {"status": "saved", "id": saved["id"], "scope": "project"}
        return {"status": "saved", "id": saved["id"]}

    def list_memories() -> dict:
        project = _project(_chat())
        memories = [{k: m[k] for k in ("id", "text", "created", "untrusted")}
                    for m in store.list()]
        if project:  # this project's, marked
            memories += [{**{k: m[k] for k in ("id", "text", "created", "untrusted")},
                          "project": True} for m in store.list(project)]
        result = {"memories": memories, "paused": store.paused}
        if any(m["untrusted"] for m in memories):
            chat = _chat()
            if chat is not None and hasattr(chat, "untrusted_saved_memory"):
                # the same sticky taint as loading one: changes ask first,
                # but it isn't fresh outside text (see _from_outside)
                chat.untrusted_saved_memory = True
            else:
                mark_untrusted(result, True)
        return result

    def update_memory(memory_id: str, text: str) -> dict:
        chat = _chat()
        refused = _incognito(chat)
        if refused:
            return refused
        old = visible(store.get(memory_id))
        if old is None:
            return {"status": "error", "message": "No memory with that id."}
        try:
            # edited with outside text in context: untrusted from now on
            store.update(memory_id, text,
                         untrusted=old["untrusted"] or _from_outside(chat))
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "updated", "id": memory_id}

    def forget_memory(memory_id: str) -> dict:
        if not valid_id(memory_id) or visible(store.get(memory_id)) is None \
                or not store.delete(memory_id):
            return {"status": "error", "message": "No memory with that id."}
        return {"status": "forgotten", "id": memory_id}

    def short(text: str, n: int = 120) -> str:
        return text if len(text) <= n else text[: n - 3] + "..."

    def ask_forget(i: dict) -> str | None:
        memory_id = i.get("memory_id")
        old = visible(store.get(memory_id)) if valid_id(memory_id) else None
        # no such memory: nothing to ask; the tool says so
        return f"Forget this: {short(old['text'])}?" if old else None

    return [
        Tool("remember",
             "Save a short memory that will be loaded into every new "
             "conversation with the user. Use it when the user says "
             "'remember...', 'always...', 'from now on...' or 'next time...', "
             "or states a lasting preference. Write one self-contained "
             f"sentence in their words (at most {MAX_MEMORY_CHARS} characters). "
             + SAVE_RULE + " In a project's conversation it is saved to that "
             "project only, unless general is true.",
             {"type": "object",
              "properties": {"note": {"type": "string",
                                      "description": "The memory to save."},
                             "general": {"type": "boolean",
                                         "description": "Only in a project's "
                                         "conversation: true when the user says it's "
                                         "about them in general, not this project."}},
              "required": ["note"]},
             remember,
             # A web page or email must not be able to plant memories.
             # the whole text (at most MAX_MEMORY_CHARS): a yes to all of it
             confirm_if_untrusted=lambda i: f"Save this to memory: {short(str(i.get('note', '')), MAX_MEMORY_CHARS)}?",
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
             confirm_if_untrusted=lambda i: f"Change this memory to: {short(str(i.get('text', '')), MAX_MEMORY_CHARS)}?",
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
