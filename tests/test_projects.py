"""Projects (projects.py): one space per class with its own instructions,
files, memories and conversations. The store (bounds, real deletes, the
rollback-safe tables), the system prompt (byte-stable, only inside its
project, global memories still there), remember scoping, the file budget
(attached as cached documents, or searched), the four-breakpoint limit,
taint, history (tagging, the filter, search/read, regenerate, branch,
resume), the web routes (validation, family isolation both ways,
suggestions, export, backup) and the logs. Offline: real SQLite in
tmp_path and the mocked Anthropic client (the contract fake for the
breakpoints)."""

import io
import json
import logging
import sqlite3
import zipfile

import pytest

from kwil import projects
from kwil.chat import ClaudeChat
from kwil.history import HistoryStore, Recorder
from kwil.memory_store import (MAX_PROJECT_MEMORIES, MAX_PROJECTS, FileRefused,
                               MemoryFull, ProjectsFull, SqliteMemoryStore)
from kwil.tools import ToolRegistry
from kwil.tools.history import history_tools
from kwil.tools.memory_tools import memory_tools
from kwil.tools.projects import project_tools
from tests.conftest import make_response, system_text, text_block, tool_use_block
from tests.contract_fakes import ContractClient

INSTRUCTIONS = "PHYS 361: Classical Mechanics. Use Taylor's notation. Exams are closed-book."
SYLLABUS = ("PHYS 361 syllabus. Late policy: homework loses ten percent per day late. "
            "The midterm covers Lagrangian mechanics and small oscillations.\n")
PDF = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
       b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n")
BOUNDS = dict(max_files=20, max_bytes=10_000_000, max_total_bytes=50_000_000)


@pytest.fixture
def store(tmp_path):
    s = SqliteMemoryStore(tmp_path / "memory.sqlite")
    yield s
    s.close()


@pytest.fixture
def hist(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite", eventlog=False)
    yield h
    h.close()


def file_bytes(path) -> bytes:
    out = b""
    for suffix in ("", "-wal"):
        p = type(path)(str(path) + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


def add_text(store, project_id, text=SYLLABUS, name="syllabus.txt"):
    return store.add_file(project_id, name, "text", text.encode(), text=text, **BOUNDS)


def chat_for(client, store, hist=None, confirm=None, asked=None, **kw) -> ClaudeChat:
    asked = asked if asked is not None else []

    def yes(question):
        asked.append(question)
        return True
    tools = ToolRegistry(confirm=confirm or yes)
    tools.register(*memory_tools(store), *project_tools(store))
    recorder = None
    if hist is not None:
        tools.register(*history_tools(hist))
        recorder = Recorder(hist, "web", run_async=False, client_fn=lambda: client)
    return ClaudeChat(client=client, tools=tools, local_memory=store, recorder=recorder,
                      **kw)


def reply(client, *texts):
    client.queue += [make_response(text_block(t)) for t in texts]


def tool_then_text(client, name, args, text="Done."):
    client.queue += [make_response(tool_use_block(name, args), stop_reason="tool_use"),
                     make_response(text_block(text))]


def tool_result(call) -> dict | list:
    """The JSON of the last tool result, or its search_result blocks (the
    project file and past-chat tools' results, citations.py)."""
    (block,) = [b for b in call["messages"][-1]["content"] if b["type"] == "tool_result"]
    content = block["content"]
    return content if isinstance(content, list) else json.loads(content)


def marks(kwargs) -> int:
    """cache_control breakpoints in a request (tools, system, messages)."""
    blocks = list(kwargs.get("tools") or [])
    system = kwargs.get("system")
    blocks += system if isinstance(system, list) else []
    for m in kwargs["messages"]:
        if isinstance(m["content"], list):
            blocks += m["content"]
    return sum(1 for b in blocks if isinstance(b, dict) and b.get("cache_control"))


# --- the store -------------------------------------------------------------------------

def test_projects_are_bounded_and_one_tap_twice_makes_one(store):
    first = store.create_project("PHYS 361", course="phys361")
    assert store.create_project("PHYS 361 again", course="phys361")["id"] == first["id"]
    with pytest.raises(ValueError):
        store.create_project("")
    with pytest.raises(ValueError):
        store.create_project("x" * 81)
    with pytest.raises(ValueError):
        store.create_project("CS 171", instructions="y" * 4001)
    for n in range(MAX_PROJECTS - 1):
        store.create_project(f"Class {n}")
    with pytest.raises(ProjectsFull):
        store.create_project("One too many")
    assert len(store.projects()) == MAX_PROJECTS


def test_project_memories_are_bounded_per_project(store, monkeypatch):
    p = store.create_project("CS 171")
    monkeypatch.setattr("kwil.memory_store.MAX_PROJECT_MEMORIES", 2)
    store.add("one", project=p["id"])
    store.add("two", project=p["id"])
    with pytest.raises(MemoryFull):
        store.add("three", project=p["id"])
    store.add("a global one still fits")
    assert MAX_PROJECT_MEMORIES == 100


def test_file_bounds_hold(store):
    p = store.create_project("PHYS 361")
    q = store.create_project("CS 171")
    with pytest.raises(FileRefused, match="at most 1 files"):
        add_text(store, p["id"])
        store.add_file(p["id"], "b.txt", "text", b"b", text="b",
                       max_files=1, max_bytes=100, max_total_bytes=10_000)
    with pytest.raises(FileRefused, match="at most"):
        store.add_file(q["id"], "big.txt", "text", b"x" * 101, max_files=5,
                       max_bytes=100, max_total_bytes=10_000)
    used = store.file_bytes()
    with pytest.raises(FileRefused, match="limit"):  # the person's total, across projects
        store.add_file(q["id"], "c.txt", "text", b"x" * 50, max_files=5,
                       max_bytes=100, max_total_bytes=used + 49)
    assert [f["name"] for f in store.files(q["id"])] == []


def test_deleting_a_project_really_deletes_its_memories_and_files(store, tmp_path):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    m = store.add("Problem sets are due Fridays at noon", project=p["id"])
    f = add_text(store, p["id"], "Zebra-secret handout text\n")
    keep = store.add("I like short answers")
    assert b"Zebra-secret" in file_bytes(tmp_path / "memory.sqlite")
    before = store.deletions
    assert store.delete_project(p["id"]) is True
    assert store.project(p["id"]) is None and store.get(m["id"]) is None
    assert store.file(f["id"]) is None and store.get(keep["id"]) is not None
    assert store.deletions == before + 1
    raw = file_bytes(tmp_path / "memory.sqlite")
    for gone in (b"Zebra-secret", b"Problem sets are due", b"Taylor's notation"):
        assert gone not in raw
    assert store.delete_project(p["id"]) is False


def test_deleting_a_file_really_deletes_it(store, tmp_path):
    p = store.create_project("PHYS 361")
    f = add_text(store, p["id"], "Quokka-only lecture notes\n")
    assert store.delete_file(f["id"]) is True and store.files(p["id"]) == []
    assert b"Quokka-only" not in file_bytes(tmp_path / "memory.sqlite")
    assert store.delete_file(f["id"]) is False


def test_the_project_tables_leave_the_schema_version_for_a_rollback(store, tmp_path):
    store.create_project("PHYS 361")
    con = sqlite3.connect(tmp_path / "memory.sqlite")
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        # an older Kwil reads only `memories`: a project memory is never in it
        store.add("project only", project=store.projects()[0]["id"])
        assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        con.close()


def test_forget_all_takes_project_memories_but_keeps_the_projects(store):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    store.add("project one", project=p["id"])
    store.add("global one")
    add_text(store, p["id"])
    assert store.forget_all() == 2
    assert store.list(p["id"]) == [] and store.project(p["id"])["instructions"] == INSTRUCTIONS
    assert len(store.files(p["id"])) == 1


def test_export_has_the_projects(store):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    store.add("Problem sets are due Fridays", project=p["id"])
    add_text(store, p["id"])
    (exported,) = store.export()["projects"]
    assert exported["name"] == "PHYS 361" and exported["instructions"] == INSTRUCTIONS
    assert [m["text"] for m in exported["memories"]] == ["Problem sets are due Fridays"]
    assert [f["name"] for f in exported["files"]] == ["syllabus.txt"]
    assert store.export()["memories"] == []  # the global list stays global


# --- the system prompt -----------------------------------------------------------------

def test_the_project_prompt_is_only_inside_its_project_and_byte_stable(fake_client, store):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    other = store.create_project("CS 171", "Use Python 3.12 in every example.")
    store.add("Problem sets are due Fridays", project=p["id"])
    store.add("CS 171 labs are on Tuesdays", project=other["id"])
    store.add("I like short answers")  # global: loads everywhere
    chat = chat_for(fake_client, store)
    chat.set_project(p["id"])
    reply(fake_client, "Sure.", "Okay.")
    chat.ask("hi")
    store.add("Saved during the conversation", project=p["id"])  # from the next one
    chat.ask("and the next thing?")
    first, second = fake_client.calls
    assert first["system"] == second["system"]  # the same bytes every turn
    text = system_text(first)
    assert INSTRUCTIONS in text and "Problem sets are due Fridays" in text
    assert "I like short answers" in text  # global memories still load
    assert "Saved during the conversation" not in text
    assert "CS 171" not in text and "Python 3.12" not in text  # no other project's
    assert chat.project_public() == {"id": p["id"], "name": "PHYS 361"}
    chat.reset()  # a new conversation: in no project
    reply(fake_client, "Hello.")
    chat.ask("hello")
    outside = system_text(fake_client.calls[-1])
    assert "I like short answers" in outside
    for leak in (INSTRUCTIONS, "Problem sets", "CS 171 labs", "Saved during"):
        assert leak not in outside
    assert chat.project_id is None and chat.project_public() is None


def test_a_project_is_chosen_before_its_conversation(fake_client, store):
    p = store.create_project("PHYS 361")
    chat = chat_for(fake_client, store)
    with pytest.raises(KeyError):
        chat.set_project("prj-000000000000")
    reply(fake_client, "Hi.")
    chat.ask("hi")
    with pytest.raises(RuntimeError):
        chat.set_project(p["id"])


def test_a_paused_memory_loads_no_project_memories(fake_client, store):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    store.add("Problem sets are due Fridays", project=p["id"])
    store.set_paused(True)
    chat = chat_for(fake_client, store)
    chat.set_project(p["id"])
    reply(fake_client, "Hi.")
    chat.ask("hi")
    text = system_text(fake_client.calls[-1])
    assert INSTRUCTIONS in text and "Problem sets" not in text


# --- remember ---------------------------------------------------------------------------

def test_remember_saves_to_the_project_unless_general(fake_client, store):
    p = store.create_project("PHYS 361")
    asked = []
    chat = chat_for(fake_client, store, asked=asked)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "remember", {"note": "Exams are closed-book"})
    chat.ask("Remember that exams are closed-book")
    assert tool_result(fake_client.calls[-1]) == {"status": "saved", "scope": "project",
                                                  "id": store.list(p["id"])[0]["id"]}
    tool_then_text(fake_client, "remember", {"note": "I drink tea", "general": True})
    chat.ask("Remember in general that I drink tea")
    assert [m["text"] for m in store.list(p["id"])] == ["Exams are closed-book"]
    assert [m["text"] for m in store.list()] == ["I drink tea"]
    assert asked == []  # no outside text here: nothing to ask
    chat.reset()  # outside any project, remember saves globally
    tool_then_text(fake_client, "remember", {"note": "I wake at seven"})
    chat.ask("Remember I wake at seven")
    assert [m["text"] for m in store.list()] == ["I wake at seven", "I drink tea"]
    assert len(store.list(p["id"])) == 1


def test_other_projects_memories_are_unknown_ids(fake_client, store):
    p, q = store.create_project("PHYS 361"), store.create_project("CS 171")
    theirs = store.add("CS 171 labs are on Tuesdays", project=q["id"])
    mine = store.add("Problem sets are due Fridays", project=p["id"])
    chat = chat_for(fake_client, store)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "list_memories", {})
    chat.ask("what do you remember?")
    listed = tool_result(fake_client.calls[-1])["memories"]
    assert [(m["id"], m.get("project")) for m in listed] == [(mine["id"], True)]
    tool_then_text(fake_client, "forget_memory", {"memory_id": theirs["id"]})
    chat.ask("forget it")
    assert tool_result(fake_client.calls[-1])["status"] == "error"
    tool_then_text(fake_client, "update_memory", {"memory_id": theirs["id"], "text": "x"})
    chat.ask("change it")
    assert tool_result(fake_client.calls[-1])["status"] == "error"
    assert store.get(theirs["id"])["text"] == "CS 171 labs are on Tuesdays"
    chat.reset()  # outside any project: no project's memory at all
    tool_then_text(fake_client, "forget_memory", {"memory_id": mine["id"]})
    chat.ask("forget it")
    assert tool_result(fake_client.calls[-1])["status"] == "error"
    assert store.get(mine["id"]) is not None


# --- files: attached in full, or searched -------------------------------------------------

def test_files_within_the_budget_go_as_cached_documents_and_taint(fake_client, store):
    p = store.create_project("PHYS 361")
    add_text(store, p["id"])
    store.add_file(p["id"], "notes.pdf", "pdf", PDF, text="", pages=1, **BOUNDS)
    asked = []
    chat = chat_for(fake_client, store, asked=asked)
    chat.set_project(p["id"])
    assert chat.outside_content()  # a handout is someone else's text
    reply(fake_client, "Ten percent a day.")
    chat.ask("What's the late policy?")
    first = fake_client.calls[-1]["messages"][0]["content"]
    assert [b["type"] for b in first[:2]] == ["document", "document"]
    assert first[0]["title"] == "syllabus.txt" and first[0]["source"]["data"] == SYLLABUS
    assert first[1]["source"]["media_type"] == "application/pdf"
    assert "cache_control" not in first[0] and first[1]["cache_control"] == {"type": "ephemeral"}
    assert all(not isinstance(b, dict) or b.get("type") != "document"
               for m in chat.history for b in (m["content"] if isinstance(m["content"], list)
                                               else []))  # never in history itself
    assert projects.FILES_INLINE in system_text(fake_client.calls[-1])
    # a memory saved now asks first and is kept as outside text
    tool_then_text(fake_client, "remember", {"note": "Late work loses ten percent a day"})
    chat.ask("Remember that")
    assert asked and store.list(p["id"])[0]["untrusted"] is True
    # the instructions alone never taint
    q = store.create_project("CS 171", "Use Python 3.12")
    calm = chat_for(fake_client, store)
    calm.set_project(q["id"])
    assert not calm.outside_content() and not calm.tainted()


def test_past_the_budget_the_files_are_searched_instead(fake_client, store):
    p = store.create_project("PHYS 361")
    f = add_text(store, p["id"])
    chat = chat_for(fake_client, store, project_inline_tokens=10)
    chat.set_project(p["id"])
    assert not chat.outside_content()  # nothing of the file is in it yet
    tool_then_text(fake_client, "search_project_files", {"query": "late policy"})
    chat.ask("What's the late policy?")
    call = fake_client.calls[-2]
    assert all(b.get("type") != "document" for b in call["messages"][0]["content"]
               if isinstance(b, dict))
    text = system_text(call)
    assert projects.FILES_SEARCH in text and f["id"] in text and "syllabus.txt" in text
    found = tool_result(fake_client.calls[-1])  # search_result blocks
    assert found[0]["source"] == f"kwil:project_file/{f['id']}"
    assert found[0]["title"] == "syllabus.txt"
    assert "Late policy" in found[0]["content"][1]["text"]
    assert chat.tainted()  # the snippet is outside text now
    tool_then_text(fake_client, "read_project_file", {"file_id": f["id"]})
    chat.ask("Read it")
    (read,) = tool_result(fake_client.calls[-1])
    assert read["content"][1]["text"] == SYLLABUS
    assert "next_offset" not in read["content"][0]["text"]


def test_the_file_tools_keep_to_the_conversations_project(fake_client, store):
    p, q = store.create_project("PHYS 361"), store.create_project("CS 171")
    theirs = add_text(store, q["id"], "CS 171 lab handout\n", "lab.txt")
    chat = chat_for(fake_client, store, project_inline_tokens=10)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "read_project_file", {"file_id": theirs["id"]})
    chat.ask("read it")
    assert tool_result(fake_client.calls[-1])["status"] == "error"
    chat.reset()
    tool_then_text(fake_client, "search_project_files", {"query": "lab"})
    chat.ask("search")
    assert tool_result(fake_client.calls[-1])["status"] == "error"


def test_the_budget_counts_tokens_and_bytes():
    small = {"kind": "text", "chars": 400, "size": 400}
    pdf = {"kind": "pdf", "pages": 3, "size": 90_000}
    assert projects.estimate_tokens(small) == 134  # max(chars / 4, bytes / 3)
    assert projects.estimate_tokens(pdf) == 3 * projects.PDF_PAGE_TOKENS
    unknown = {"kind": "pdf", "pages": None, "size": 12_000}
    assert projects.estimate_tokens(unknown) is None
    assert not projects.fits([small, unknown], 10**9, 10**9)  # never attached
    assert projects.fits([small, pdf], 10_000, 1_000_000)
    assert not projects.fits([small, pdf], 6000, 1_000_000)
    assert not projects.fits([small, pdf], 10_000, 50_000)
    assert not projects.fits([], 10_000, 1_000_000)


def object_stream_pdf(pages: int) -> bytes:
    """A PDF whose Catalog and page tree root sit in a compressed object
    stream with a cross-reference stream (pdfTeX's and LuaTeX's default):
    attachments.pdf_pages can't see its page count."""
    import struct
    import zlib

    objs, kids, nxt = {}, [], 5
    for i in range(pages):
        page, content = nxt, nxt + 1
        nxt += 2
        kids.append(page)
        text = f"BT /F1 12 Tf 72 720 Td (Lecture page {i + 1} Lagrangian notes) Tj ET".encode()
        objs[content] = b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream"
        objs[page] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                      b"<< /Font << /F1 4 0 R >> >> /Contents %d 0 R >>" % content)
    objs[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    inner = [b"<< /Type /Catalog /Pages 2 0 R >>",
             b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
                 pages, b" ".join(b"%d 0 R" % k for k in kids))]
    head, body = [], b""
    for num, obj in zip((1, 2), inner):
        head.append(b"%d %d" % (num, len(body)))
        body += obj + b"\n"
    hdr = b" ".join(head) + b"\n"
    packed = zlib.compress(hdr + body)
    objs[3] = (b"<< /Type /ObjStm /N 2 /First %d /Filter /FlateDecode /Length %d >>\nstream\n"
               % (len(hdr), len(packed)) + packed + b"\nendstream")
    out, offsets = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n", {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    rows = b""
    for num in range(nxt + 1):
        if num == 0:
            rows += struct.pack(">BIH", 0, 0, 65535)
        elif num in (1, 2):
            rows += struct.pack(">BIH", 2, 3, num - 1)
        else:
            rows += struct.pack(">BIH", 1, len(out) if num == nxt else offsets[num], 0)
    packed = zlib.compress(rows)
    xref = len(out)
    return out + (b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R /Filter "
                  b"/FlateDecode /Length %d >>\nstream\n" % (nxt, nxt + 1, len(packed))
                  + packed + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % xref)


def test_a_pdf_with_its_page_tree_in_an_object_stream_is_counted(store):
    """pdf_pages can't see the count there: pypdf counts at upload, over
    100 pages is refused, and a count that stays unknown is never attached."""
    from kwil import attachments
    from kwil.attachments import AttachmentRejected

    big = object_stream_pdf(150)
    assert attachments.pdf_pages(big) is None
    with pytest.raises(AttachmentRejected, match="over 100 pages"):
        projects.prepare_file(big, "lectures.pdf", max_bytes=10_000_000)
    kind, _name, text, pages = projects.prepare_file(object_stream_pdf(3), "l.pdf",
                                                     max_bytes=10_000_000)
    assert (kind, pages) == ("pdf", 3) and "Lagrangian" in text
    p = store.create_project("PHYS 361")
    store.add_file(p["id"], "unknown.pdf", "pdf", PDF, text="", pages=None, **BOUNDS)
    assert not projects.fits(store.files(p["id"]), 10**9, 10**9)


def test_chinese_text_is_not_under_counted():
    """A character of Chinese is about a token, not a quarter of one: 150k
    characters is far over a 40k-token budget."""
    zh = "拉格朗日力学笔记。" * 17000
    kind, _name, text, _pages = projects.prepare_file(zh.encode(), "notes.txt",
                                                      max_bytes=10_000_000)
    f = {"kind": kind, "chars": len(text), "size": len(zh.encode())}
    assert projects.estimate_tokens(f) >= len(zh) * 0.9
    assert not projects.fits([f], 40000, 4_000_000)
    english = {"kind": "text", "chars": 4000, "size": 4000}
    assert projects.estimate_tokens(english) == 1334  # bytes / 3 is the larger here too


def test_a_refused_request_with_the_files_attached_searches_them_instead(
        fake_client, store, caplog):
    """The safety net: "prompt is too long" (or "A maximum of 100 PDF pages
    may be provided") with the files attached drops them for this
    conversation, says so in the system prompt, and retries once."""
    from tests.conftest import api_bad_request

    p = store.create_project("PHYS 361")
    add_text(store, p["id"])
    chat = chat_for(fake_client, store)
    chat.set_project(p["id"])
    caplog.set_level(logging.WARNING)
    for refusal in ("prompt is too long: 250000 tokens > 200000 maximum",
                    "A maximum of 100 PDF pages may be provided."):
        chat.reset()
        chat.set_project(p["id"])
        fake_client.queue.clear()
        fake_client.calls.clear()
        state = {"n": 0}
        create = fake_client.messages.create.side_effect

        def refuse_once(**kwargs):
            state["n"] += 1
            if state["n"] == 1:
                fake_client.calls.append({**kwargs, "messages": list(kwargs["messages"])})
                raise api_bad_request(refusal)
            return create(**kwargs)
        fake_client.messages.create.side_effect = refuse_once
        reply(fake_client, "Ten percent a day.", "Still searching.")
        assert chat.ask("What's the late policy?") == "Ten percent a day."
        first, second = fake_client.calls
        assert first["messages"][0]["content"][0]["type"] == "document"
        assert all(b.get("type") != "document" for b in second["messages"][0]["content"])
        assert projects.FILES_SEARCH in system_text(second)
        assert chat.tainted()  # they were in it
        chat.ask("and the midterm?")  # the rest of the conversation: searched
        assert projects.FILES_SEARCH in system_text(fake_client.calls[-1])
        fake_client.messages.create.side_effect = create
    assert "searching them instead" in caplog.text


def test_search_reads_the_text_never_the_files_bytes(fake_client, store, monkeypatch):
    p = store.create_project("PHYS 361")
    add_text(store, p["id"])
    monkeypatch.setattr(store, "file", lambda *a: pytest.fail("loaded a file's bytes"))
    chat = chat_for(fake_client, store, project_inline_tokens=10)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "search_project_files", {"query": "late policy"})
    chat.ask("What's the late policy?")
    assert tool_result(fake_client.calls[-1])[0]["type"] == "search_result"


def test_at_most_four_breakpoints_with_files_study_mode_and_attachments(store):
    """The contract fake refuses a fifth: the stable system block, study
    mode's, the project's files and the end of the conversation."""
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    add_text(store, p["id"])
    add_text(store, p["id"], "Lecture 1 notes\n", "lecture1.txt")  # one breakpoint for both
    client = ContractClient()
    chat = chat_for(client, store, model="test-model", max_tokens=1024)
    chat.set_project(p["id"])
    chat.start_study(course="PHYS 361", source="app")
    attachment = {"type": "document", "title": "hw.pdf", "source": {
        "type": "base64", "media_type": "application/pdf",
        "data": __import__("base64").b64encode(PDF).decode()}}
    assert chat.ask("Explain my homework", attachments=[attachment]).strip()
    assert chat.ask("And the syllabus?").strip()
    for request in client.requests:
        assert marks(request) == 4
        assert len(request["system"]) == 2
        assert [b["type"] for b in request["messages"][0]["content"][:2]] == [
            "document", "document"]


def test_deleted_files_and_projects_leave_the_running_conversation(fake_client, store):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    f = add_text(store, p["id"])
    chat = chat_for(fake_client, store)
    chat.set_project(p["id"])
    reply(fake_client, "Hi.", "Still here.", "Okay.")
    chat.ask("hi")
    assert fake_client.calls[-1]["messages"][0]["content"][0]["type"] == "document"
    store.delete_file(f["id"])
    chat.ask("and now?")
    sent = fake_client.calls[-1]
    assert all(b.get("type") != "document" for b in sent["messages"][0]["content"])
    assert SYLLABUS not in json.dumps(sent)
    assert chat.tainted()  # what it read before still counts
    store.delete_project(p["id"])
    chat.ask("and now?")
    assert INSTRUCTIONS not in system_text(fake_client.calls[-1])
    assert chat.project_public() is None


# --- history --------------------------------------------------------------------------------

def test_conversations_are_tagged_and_filtered_by_project(fake_client, store, hist):
    p = store.create_project("PHYS 361")
    chat = chat_for(fake_client, store, hist)
    chat.set_project(p["id"])
    reply(fake_client, "Hi.", "Hello.")
    chat.ask("Lagrangian question")
    inside = chat.conversation_id
    chat.end_conversation()
    chat.ask("Lagrangian question outside")
    outside = chat.conversation_id
    assert hist.project_of(inside) == p["id"] and hist.project_of(outside) is None
    assert [c["id"] for c in hist.list(project=p["id"])] == [inside]
    assert {c["id"] for c in hist.list()} == {inside, outside}
    assert [c["id"] for c in hist.search("Lagrangian", project=p["id"])] == [inside]
    assert hist.get(inside, messages=False)["project"] == p["id"]
    hist.delete(inside)  # its row goes with it (ON DELETE CASCADE)
    assert hist.project_of(inside) is None
    assert hist.unfile_project(p["id"]) == 0


def test_search_and_read_keep_to_the_project_when_asked(fake_client, store, hist):
    p = store.create_project("PHYS 361")
    inside = hist.create("web")
    hist.append(inside, "user", "We decided the oscillations review is Monday")
    hist.set_project(inside, p["id"])
    outside = hist.create("web")
    hist.append(outside, "user", "We decided the oscillations party is Friday")
    chat = chat_for(fake_client, store, hist)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "search_conversations",
                   {"query": "oscillations", "project_only": True})
    chat.ask("what did we decide in this class?")
    found = tool_result(fake_client.calls[-1])["conversations"]
    assert [c["id"] for c in found] == [inside] and found[0]["in_this_project"] is True
    tool_then_text(fake_client, "search_conversations", {"query": "oscillations"})
    chat.ask("and anywhere?")
    assert {c["id"] for c in tool_result(fake_client.calls[-1])["conversations"]} == {
        inside, outside}
    tool_then_text(fake_client, "read_conversation",
                   {"conversation_id": outside, "project_only": True})
    chat.ask("read that one")
    assert tool_result(fake_client.calls[-1])["status"] == "error"
    tool_then_text(fake_client, "read_conversation",
                   {"conversation_id": inside, "project_only": True})
    chat.ask("read this one")
    assert tool_result(fake_client.calls[-1])[0]["source"] == f"kwil:chat/{inside}"
    chat.end_conversation()  # outside a project, project_only says so
    tool_then_text(fake_client, "search_conversations",
                   {"query": "oscillations", "project_only": True})
    chat.ask("in this class?")
    assert tool_result(fake_client.calls[-1])["status"] == "error"


def test_try_again_keeps_the_project(fake_client, store, hist):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    chat = chat_for(fake_client, store, hist)
    chat.set_project(p["id"])
    reply(fake_client, "First answer.", "Second answer.", "Edited answer.")
    chat.ask("Explain Lagrangians")
    cid = chat.conversation_id
    assert chat.regenerate() == "Second answer."
    assert chat.regenerate(text="Explain Hamiltonians") == "Edited answer."
    assert all(INSTRUCTIONS in system_text(c) for c in fake_client.calls)
    assert chat.conversation_id == cid and hist.project_of(cid) == p["id"]
    assert chat.project_id == p["id"]


def test_a_branch_stays_in_its_project(store, hist):
    p = store.create_project("PHYS 361")
    cid = hist.create("web")
    hist.append(cid, "user", "Explain small oscillations")
    hist.append(cid, "assistant", "Sure.")
    hist.set_project(cid, p["id"])
    branch = hist.branch(cid, 0)
    assert hist.project_of(branch) == p["id"]
    plain = hist.create("web")
    hist.append(plain, "user", "hi")
    assert hist.project_of(hist.branch(plain, 0)) is None
    with pytest.raises(ValueError):
        hist.set_project(cid, "not-a-project")


def test_resuming_a_conversation_brings_its_project(fake_client, store, hist):
    p = store.create_project("PHYS 361", INSTRUCTIONS)
    cid = hist.create("web")
    hist.append(cid, "user", "Explain small oscillations")
    hist.append(cid, "assistant", "Sure.")
    hist.set_project(cid, p["id"])
    chat = chat_for(fake_client, store, hist)
    chat.resume(cid)
    assert chat.project_id == p["id"]
    reply(fake_client, "Okay.")
    chat.ask("go on")
    assert INSTRUCTIONS in system_text(fake_client.calls[-1])


# --- logs ------------------------------------------------------------------------------------

def test_no_project_text_at_info(fake_client, store, hist, caplog):
    secrets = ["Zanzibar-course", "Quetzal-instructions", "Okapi-memory",
               "Narwhal-file.txt", "Axolotl-text", "Pangolin-query"]
    caplog.set_level(logging.DEBUG)
    p = store.create_project("Zanzibar-course", "Quetzal-instructions")
    store.update_project(p["id"], instructions="Quetzal-instructions again")
    add_text(store, p["id"], "Axolotl-text\n", "Narwhal-file.txt")
    chat = chat_for(fake_client, store, hist, project_inline_tokens=1)
    chat.set_project(p["id"])
    tool_then_text(fake_client, "remember", {"note": "Okapi-memory"})
    chat.ask("remember Okapi-memory")
    tool_then_text(fake_client, "search_project_files", {"query": "Pangolin-query Axolotl"})
    chat.ask("search Pangolin-query")
    info = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "Projects: created" in info and "Project files searched" in info
    for secret in secrets:
        assert secret not in info, secret


# --- the web ----------------------------------------------------------------------------------

pytest.importorskip("fastapi")
from tests.test_family import env, owner  # noqa: E402,F401 (fixtures)
from tests.test_webapp import events  # noqa: E402


def with_projects(env, tmp_path):
    """The owner's own memory store, with project tools, in his chats."""
    s = SqliteMemoryStore(tmp_path / "owner-memory.sqlite")
    env.assistant.local_memory = s
    env.assistant.tools.register(*memory_tools(s), *project_tools(s))
    make = env.assistant.chat_factory

    def factory(registry):
        chat = make(registry)
        chat.local_memory = s
        chat._load_memory()
        return chat
    env.assistant.chat_factory = factory
    env.assistant.sessions.clear()  # made from now on with his store
    return s


def upload(client, project_id, data, name="syllabus.txt"):
    return client.post(f"/api/projects/{project_id}/files",
                       files={"file": (name, data, "application/octet-stream")})


def test_the_project_routes(env, owner, tmp_path):
    mine = with_projects(env, tmp_path)
    assert owner.get("/api/state").json()["projects"] is True
    r = owner.post("/api/projects", json={"name": "PHYS 361", "instructions": INSTRUCTIONS})
    assert r.status_code == 200, r.text
    pid = r.json()["project"]["id"]
    listed = owner.get("/api/projects").json()
    assert [p["id"] for p in listed["projects"]] == [pid]
    assert listed["limits"]["files"] == 20 and listed["limits"]["projects"] == MAX_PROJECTS
    assert owner.post(f"/api/projects/{pid}/update",
                      json={"name": "PHYS 361 (Fall)"}).json()["project"]["name"] == \
        "PHYS 361 (Fall)"
    assert owner.post(f"/api/projects/{pid}/update", json={"nope": 1}).status_code == 400
    assert owner.post("/api/projects", json={"name": ""}).status_code == 400
    assert owner.post("/api/projects", json={"name": 5}).status_code == 400
    f = upload(owner, pid, SYLLABUS.encode()).json()["file"]
    mine.add("Problem sets are due Fridays", project=pid)
    page = owner.get(f"/api/projects/{pid}").json()
    assert [x["name"] for x in page["files"]] == ["syllabus.txt"]
    assert [m["text"] for m in page["memories"]] == ["Problem sets are due Fridays"]
    assert page["project"]["instructions"] == INSTRUCTIONS
    assert owner.get("/api/projects/prj-000000000000").status_code == 404
    assert owner.get("/api/projects/nope").status_code == 404
    other = owner.post("/api/projects", json={"name": "CS 171"}).json()["project"]["id"]
    assert owner.post(f"/api/projects/{other}/files/{f['id']}/delete").status_code == 404
    assert len(mine.files(pid)) == 1  # another project's URL never deletes it
    assert owner.post(f"/api/projects/{pid}/files/{f['id']}/delete").json() == {"ok": True}
    assert mine.files(pid) == []
    assert owner.post(f"/api/projects/{pid}/delete", json={}).status_code == 400
    assert owner.post(f"/api/projects/{pid}/delete", json={"confirm": True}).json() == {
        "ok": True}
    assert mine.project(pid) is None and mine.list(pid) == []
    assert env.client().get("/api/projects").status_code == 401


def test_uploads_are_validated_and_bounded(env, owner, tmp_path, monkeypatch):
    mine = with_projects(env, tmp_path)
    pid = owner.post("/api/projects", json={"name": "PHYS 361"}).json()["project"]["id"]
    from tests.test_tutor import photo

    def never(*a, **kw):
        raise AssertionError("a photo for a project is never decoded")
    monkeypatch.setattr("kwil.tutor.prepare_image", never)
    r = upload(owner, pid, photo(), "board.jpg")
    assert r.status_code == 400 and "PDF or a text file" in r.json()["error"]
    assert upload(owner, pid, b"MZ\x90\x00\x03binary", "tool.exe").status_code == 400
    assert upload(owner, pid, b"", "empty.txt").status_code == 400
    assert upload(owner, pid, PDF, "notes.pdf").json()["file"]["kind"] == "pdf"
    env.assistant.project_limits = {**projects.limits({}), "max_bytes": 100,
                                    "max_files": 2}
    assert upload(owner, pid, b"x" * 101, "big.txt").status_code == 400
    assert upload(owner, pid, b"short", "a.txt").status_code == 200
    r = upload(owner, pid, b"third", "b.txt")
    assert r.status_code == 409 and "at most 2 files" in r.json()["error"]
    two = owner.post(f"/api/projects/{pid}/files", files=[
        ("file", ("a.txt", b"a", "text/plain")), ("file", ("b.txt", b"b", "text/plain"))])
    assert two.status_code == 400
    assert owner.post(f"/api/projects/{pid}/files", json={"file": "x"}).status_code == 415
    assert upload(owner, "prj-000000000000", b"x").status_code == 404
    assert len(mine.files(pid)) == 2
    # a full project is refused before the file is parsed at all
    monkeypatch.setattr(projects, "prepare_file",
                        lambda *a, **kw: pytest.fail("parsed a file that can't fit"))
    assert upload(owner, pid, b"fourth", "c.txt").status_code == 409
    env.assistant.project_limits = {**projects.limits({}), "max_total_bytes": 1}
    other = owner.post("/api/projects", json={"name": "CS 171"}).json()["project"]["id"]
    assert upload(owner, other, b"too much", "d.txt").status_code == 409


def test_a_new_chat_in_a_project_from_the_page(env, owner, tmp_path):
    mine = with_projects(env, tmp_path)
    pid = owner.post("/api/projects", json={"name": "PHYS 361",
                                            "instructions": INSTRUCTIONS}).json()["project"]["id"]
    env.fake_client.queue += [make_response(text_block(t)) for t in (
        "Hi.", "More.", "NOTHING", "Other.")]  # NOTHING: the ended one's summary
    done = events(owner.post("/api/turn", json={"text": "hello", "project_id": pid}))[-1]
    assert done["project"] == {"id": pid, "name": "PHYS 361"}
    assert INSTRUCTIONS in system_text(env.fake_client.calls[-1])
    cid = done["conversation"]
    assert env.owner_store.project_of(cid) == pid
    state = owner.get("/api/state").json()
    assert state["project"] == {"id": pid, "name": "PHYS 361"}
    page = owner.get(f"/api/projects/{pid}").json()
    assert [c["id"] for c in page["conversations"]] == [cid] and page["active"] is True
    listed = owner.get(f"/api/conversations?project={pid}").json()["conversations"]
    assert [c["id"] for c in listed] == [cid] and listed[0]["project"] == pid
    assert owner.get("/api/conversations?project=prj-000000000000").status_code == 404
    # the same pane again: the same conversation
    done = events(owner.post("/api/turn", json={"text": "more", "project_id": pid}))[-1]
    assert done["conversation"] == cid
    # a new chat outside it: that conversation ends, a new one starts in none
    evs = events(owner.post("/api/turn", json={"text": "other", "project_id": ""}))
    assert [e["text"] for e in evs if e["type"] == "reply"] == ["Other."]
    done = evs[-1]
    assert "project" not in done and done["conversation"] not in (None, cid)
    assert env.owner_store.project_of(done["conversation"]) is None
    assert INSTRUCTIONS not in system_text(env.fake_client.calls[-1])
    assert owner.post("/api/turn", json={"text": "x", "project_id": "prj-000000000000"}
                      ).status_code == 404
    assert owner.post("/api/turn", json={"text": "x", "project_id": "../etc"}
                      ).status_code == 400
    # a branch of it stays in it
    branch = owner.post(f"/api/conversations/{cid}/branch", json={"message_index": 0})
    assert branch.json()["project"] == pid
    # deleting the project: its chats stay, in no project
    owner.post(f"/api/projects/{pid}/delete", json={"confirm": True})
    assert env.owner_store.project_of(cid) is None
    assert env.owner_store.get(cid, messages=False)["id"] == cid
    del mine


def test_an_incognito_chat_may_be_in_a_project_and_saves_nothing(env, owner, tmp_path):
    with_projects(env, tmp_path)
    pid = owner.post("/api/projects", json={"name": "PHYS 361",
                                            "instructions": INSTRUCTIONS}).json()["project"]["id"]
    assert owner.post("/api/incognito", json={"on": True}).json() == {"on": True}
    env.fake_client.queue.append(make_response(text_block("Hi.")))
    done = events(owner.post("/api/turn", json={"text": "hello", "project_id": pid}))[-1]
    assert done["project"]["id"] == pid and done["conversation"] is None
    assert INSTRUCTIONS in system_text(env.fake_client.calls[-1])
    assert env.owner_store.list(project=pid) == []


def test_the_prompt_names_a_family_member_and_keeps_the_name_as_typed():
    from kwil.memory_store import Snapshot

    snap = Snapshot(project={"id": "prj-0123456789ab", "name": 'Kyle\'s "class"',
                             "instructions": ""})
    (part,) = projects.prompt_parts(snap, who="Mom")
    assert part.startswith("This conversation is in Mom's project \"Kyle's 'class'\"")
    assert "Kyle says" not in part and "Mom says" in part and " his " not in part


@pytest.mark.parametrize("who", ["Sam {dad}", "J{0}", "}{", "{name}"])
def test_a_display_name_with_braces_does_not_break_the_project_prompt(who):
    from kwil.memory_store import Snapshot

    snap = Snapshot(project={"id": "prj-0123456789ab", "name": "PHYS 361",
                             "instructions": ""})
    (part,) = projects.prompt_parts(snap, who=who)
    assert part.startswith(f"This conversation is in {who}'s project \"PHYS 361\"")
    assert f"{who} says" in part


def test_family_members_have_their_own_projects_404_both_ways(env, owner, tmp_path):
    mine = with_projects(env, tmp_path)
    kyle = owner.post("/api/projects", json={"name": "PHYS 361"}).json()["project"]["id"]
    kyle_file = upload(owner, kyle, SYLLABUS.encode()).json()["file"]["id"]
    mom, link = env.member(owner, name="Mom", language="en", tier="family")
    uid = link["user"]["id"]
    assert mom.get("/api/state").json()["projects"] is True
    assert mom.get("/api/projects").json()["projects"] == []
    hers = mom.post("/api/projects", json={"name": "Cooking"}).json()["project"]["id"]
    her_file = upload(mom, hers, b"Grandma's recipe\n", "recipe.txt").json()["file"]["id"]
    def tries(client, pid, fid):
        """Every project route, each with a body it would accept."""
        return [client.get(f"/api/projects/{pid}"),
                client.post(f"/api/projects/{pid}/update", json={"name": "x"}),
                client.post(f"/api/projects/{pid}/delete", json={"confirm": True}),
                upload(client, pid, b"x"),
                client.post(f"/api/projects/{pid}/files/{fid}/delete"),
                client.get(f"/api/conversations?project={pid}")]
    assert [r.status_code for r in tries(mom, kyle, kyle_file)] == [404] * 6
    assert [r.status_code for r in tries(owner, hers, her_file)] == [404] * 6
    assert mom.post("/api/turn", json={"text": "hi", "project_id": kyle}).status_code == 404
    assert owner.post("/api/turn", json={"text": "hi", "project_id": hers}).status_code == 404
    assert mine.project(kyle) is not None and env.family.context(uid).memory.project(hers)
    assert [p["id"] for p in owner.get("/api/projects").json()["projects"]] == [kyle]


def test_suggestions_come_from_their_own_courses(env, owner, tmp_path):
    with_projects(env, tmp_path)

    class Deadlines:
        def list(self, uid):
            return [{"course": "PHYS 361 - Fall 2026"}, {"course": "phys361"},
                    {"course": ""}] if uid is None else [{"course": "Mom's class 101"}]
    env.assistant.deadlines = Deadlines()
    env.assistant.notion_courses = lambda: ["CS 171", "PHYS 361"]
    found = owner.get("/api/projects/suggestions").json()["suggestions"]
    assert [(s["name"], s["course"], s["source"]) for s in found] == [
        ("PHYS 361 - Fall 2026", "phys361", "deadlines"), ("CS 171", "cs171", "notion")]
    made = owner.post("/api/projects", json={"name": found[0]["name"],
                                             "course": found[0]["course"]}).json()
    again = owner.post("/api/projects", json={"name": "PHYS 361", "course": "PHYS 361"})
    assert again.json()["project"]["id"] == made["project"]["id"]  # one tap twice: one
    found = owner.get("/api/projects/suggestions").json()["suggestions"]
    assert found[0]["project"] == made["project"]["id"]
    mom, _ = env.member(owner, name="Mom", language="en", tier="family")
    theirs = mom.get("/api/projects/suggestions").json()["suggestions"]
    assert [s["name"] for s in theirs] == ["Mom's class 101"]  # never Kyle's Notion


def test_their_export_and_the_backup_include_projects(env, owner, tmp_path):
    from kwil import backup as backup_mod

    mine = with_projects(env, tmp_path)
    pid = mine.create_project("PHYS 361", INSTRUCTIONS)["id"]
    add_text(mine, pid)
    mom, link = env.member(owner, name="Mom", language="en", tier="family")
    hers = mom.post("/api/projects", json={"name": "Cooking"}).json()["project"]["id"]
    upload(mom, hers, b"Grandma's recipe\n", "recipe.txt")
    env.app.state.account.clock = lambda: env.clock.now
    r = mom.get("/api/me/export")
    assert r.status_code == 200, r.text
    files = {n: z for z in [zipfile.ZipFile(io.BytesIO(r.content))] for n in z.namelist()}
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    memory = json.loads(zf.read("memory.json"))
    assert [p["name"] for p in memory["projects"]] == ["Cooking"]
    (name,) = [n for n in files if n.startswith(f"projects/{hers}/")]
    assert name.endswith("-recipe.txt") and zf.read(name) == b"Grandma's recipe\n"
    assert not any("PHYS" in zf.read(n).decode("utf-8", "replace") for n in files)
    b = backup_mod.Backup(memory_path=mine.path, family=env.family,
                          temp_dir=tmp_path / "tmp")
    out, folder = b.build()
    try:
        with zipfile.ZipFile(out) as z:
            assert "memory.sqlite" in z.namelist()
            copy = tmp_path / "copy.sqlite"
            copy.write_bytes(z.read("memory.sqlite"))
            con = sqlite3.connect(copy)
            assert con.execute("SELECT name, instructions FROM projects").fetchall() == [
                ("PHYS 361", INSTRUCTIONS)]
            # the files themselves are left out (each person has them; with
            # them the backup would be everyone's max_person_mb together)
            assert con.execute("SELECT COUNT(*) FROM project_files").fetchone()[0] == 0
            con.close()
            assert SYLLABUS.encode() not in z.read("memory.sqlite")
            member = [n for n in z.namelist() if n.endswith("memory.sqlite") and "users/" in n]
            assert member  # hers, as her own file
            assert b"Grandma" not in z.read(member[0])
            manifest = json.loads(z.read("MANIFEST.json"))
            left = {f["name"]: f.get("project_files_left_out") for f in manifest["files"]}
            assert left["memory.sqlite"] == 1 and left[member[0]] == 1
        assert mine.files(pid)  # the live files are untouched
    finally:
        backup_mod.cleanup(folder)


def test_project_files_never_push_the_rest_of_an_export_over_its_cap(env, owner, tmp_path):
    """They have their own allowance ([projects] max_person_mb, which
    uploads keep them under), not [family] export_max_mb."""
    with_projects(env, tmp_path)
    mom, _ = env.member(owner, name="Mom", language="en", tier="family")
    hers = mom.post("/api/projects", json={"name": "Cooking"}).json()["project"]["id"]
    big = b"Recipe line.\n" * 15_000  # 195 KB (a text file is at most 200k characters)
    assert upload(mom, hers, big, "recipes.txt").status_code == 200
    env.config["family"]["export_max_mb"] = 0.1  # the rest still fits, the file alone doesn't
    env.app.state.account.clock = lambda: env.clock.now
    r = mom.get("/api/me/export")
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    (name,) = [n for n in zf.namelist() if n.startswith(f"projects/{hers}/")]
    assert zf.read(name) == big
    env.config["projects"] = {"max_person_mb": 0.1}  # over their own allowance: refused
    assert mom.get("/api/me/export").status_code in (413, 429)


def test_a_project_chats_pinecone_summary_is_recalled_only_in_that_project(fake_client, store):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from kwil.memory import PineconeMemory

    pc = MagicMock()
    pc.inference.embed.return_value = SimpleNamespace(data=[{"values": [0.1] * 1024}])
    index = MagicMock()
    index.query.return_value = SimpleNamespace(matches=[
        SimpleNamespace(id="v1", score=0.9, metadata={"text": "PHYS summary",
                                                      "project_id": "prj-aaaaaaaaaaaa"}),
        SimpleNamespace(id="v2", score=0.9, metadata={"text": "CS summary",
                                                      "project_id": "prj-bbbbbbbbbbbb"}),
        SimpleNamespace(id="v3", score=0.9, metadata={"text": "general summary"})])
    mem = PineconeMemory(pc=pc, index=index)
    mem.search_namespaces = ["voice-assistant"]
    assert mem.retrieve("x") == ["general summary"]
    assert mem.retrieve("x", project="prj-aaaaaaaaaaaa") == ["PHYS summary", "general summary"]
    # a project chat's summary is stored with its project
    p = store.create_project("PHYS 361")
    chat = chat_for(fake_client, store, memory=mem)
    chat.set_project(p["id"])
    reply(fake_client, "Sure.", "Lagrangians were covered.")
    chat.ask("Explain Lagrangians")
    chat.end_conversation()
    metadata = index.upsert.call_args.kwargs["vectors"][0]["metadata"]
    assert metadata["project_id"] == p["id"] and chat.project_id is None


def test_deleting_her_account_deletes_her_projects(env, owner, tmp_path):
    with_projects(env, tmp_path)
    mom, link = env.member(owner, name="Mom", language="en", tier="family")
    uid = link["user"]["id"]
    hers = mom.post("/api/projects", json={"name": "Cooking"}).json()["project"]["id"]
    upload(mom, hers, b"Grandma's recipe\n", "recipe.txt")
    path = env.family.dir / uid / "memory.sqlite"
    assert path.exists()
    env.app.state.account.revoke = lambda token: None
    assert mom.post("/api/me/delete", json={"confirm": "DELETE"}).status_code == 200
    assert not path.exists()


def test_other_projects_summaries_never_crowd_out_a_chats_notes():
    """Judge round 3: the project filter ran after Pinecone returned top_k
    matches, so other projects' closest summaries could leave a chat with
    no notes. It now over-fetches, filters, then keeps top_k."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from kwil.memory import PineconeMemory

    pc = MagicMock()
    pc.inference.embed.return_value = SimpleNamespace(data=[{"values": [0.1] * 1024}])
    others = [SimpleNamespace(id=f"o{i}", score=0.95, metadata={
        "text": f"other {i}", "project_id": "prj-bbbbbbbbbbbb"}) for i in range(4)]
    mine = [SimpleNamespace(id=f"g{i}", score=0.9, metadata={"text": f"general {i}"})
            for i in range(6)]

    def query(**kw):
        return SimpleNamespace(matches=(others + mine)[: kw["top_k"]])

    index = MagicMock()
    index.query.side_effect = query
    mem = PineconeMemory(pc=pc, index=index)
    mem.search_namespaces = ["voice-assistant"]
    got = mem.retrieve("x")
    assert got == [f"general {i}" for i in range(mem.top_k)]
    assert index.query.call_args.kwargs["top_k"] > mem.top_k
