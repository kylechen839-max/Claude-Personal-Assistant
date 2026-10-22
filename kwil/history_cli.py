"""`python -m kwil --history list|search|show|export|delete`."""

from __future__ import annotations

import sys

from kwil import history

USAGE = ("usage: python -m kwil --history list | search WORDS | show ID | "
         "export ID [--format md|json] | delete ID [--yes]")


def _filters(args, tz=None) -> dict:
    return {"source": args.source or None,
            "pinned": True if args.pinned else None,
            "since": history.parse_day(args.since, tz=tz),
            "until": history.parse_day(args.until, end=True, tz=tz)}


def _row(conv: dict, highlight: bool) -> str:
    dt = history.local_dt(conv["updated_at"])
    title = conv["title"] or "(untitled)"
    pin = "*" if conv["pinned"] else " "
    line = (f"{conv['id']}  {dt:%Y-%m-%d %H:%M}  {conv['source']:<5} {pin} {title}"
            f"  ({conv['message_count']} messages)")
    snippet = conv.get("snippet")
    if snippet:
        start, end = ("\033[1m", "\033[0m") if highlight else ("[", "]")
        line += "\n" + " " * 18 + history.plain_snippet(snippet, start, end)
    return line


def run(args, config: dict, output_fn=print, input_fn=None, memory_fn=None) -> int:
    input_fn = input_fn or input
    action, rest = args.history[0], args.history[1:]
    store = history.open_store(config)
    if store is None:
        output_fn("Conversation history is off ([history] enabled = false).")
        return 1
    try:
        return _run(store, action, rest, args, output_fn, input_fn, memory_fn)
    except KeyError:
        output_fn(f"No conversation with id {rest[0] if rest else ''!r}.")
        return 1
    finally:
        store.close()


def _run(store, action, rest, args, output_fn, input_fn, memory_fn) -> int:
    highlight = sys.stdout.isatty()
    if action == "list":
        convs = store.list(limit=50, **_filters(args))
        if not convs:
            output_fn("No saved conversations yet.")
        for conv in convs:
            output_fn(_row({**conv, "snippet": ""}, highlight))
        return 0
    if action == "search":
        if not rest:
            output_fn(USAGE)
            return 2
        memory = memory_fn() if memory_fn else None
        hits = history.search_conversations(store, " ".join(rest), memory,
                                            limit=20, **_filters(args))
        if not hits:
            output_fn("No conversations match.")
        for conv in hits:
            output_fn(_row(conv, highlight))
        return 0
    if action not in ("show", "export", "delete") or len(rest) != 1:
        output_fn(USAGE)
        return 2
    cid = rest[0]
    if action == "show":
        output_fn(history.export(store.get(cid), "md").rstrip())
        return 0
    if action == "export":
        output_fn(history.export(store.get(cid), args.format).rstrip())
        return 0
    conv = store.get(cid, messages=False)
    title = conv["title"] or "(untitled)"
    if not args.yes and input_fn(f"Delete '{title}'? This can't be undone. "
                                 f"(yes/no): ").strip().lower() not in ("y", "yes"):
        output_fn("Not deleted.")
        return 0
    try:
        history.delete_conversation(store, cid, memory_fn() if memory_fn else None)
    except Exception as e:  # Pinecone unreachable: keep both, try again later
        output_fn(f"Could not delete its memory summary ({e}); nothing was deleted.")
        return 1
    output_fn(f"Deleted '{title}'.")
    return 0
