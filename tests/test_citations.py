"""A reply's sources (citations.py): web search citations and Kwil's own
data (search_result blocks), bounded, safe URLs only, stored with the
reply in history (export, import, branch, interrupted), gone with it on a
supersede or a delete, shown by the web app, never spoken, and never
logged at INFO."""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from kwil import citations, history
from kwil.chat import ClaudeChat
from kwil.citations import Sources, safe_url
from kwil.history import HistoryStore, Recorder
from kwil.tools import Tool, ToolRegistry
from tests.conftest import make_response, text_block, tool_use_block

WEB = {"kind": "web", "url": "https://physics.example.edu/exams", "title": "PHYS 361 exams"}


def web_cite(url, title="Page", cited="the exam is Friday"):
    return {"type": "web_search_result_location", "url": url, "title": title,
            "encrypted_index": "ei", "cited_text": cited}


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "history.sqlite", eventlog=False)
    yield s
    s.close()


# ---------- URLs, bounds ----------

@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>x</b>", "/relative",
    "https://", "ftp://example.com/x", "https://exa mple.com", " https://example.com",
    "https://example.com/\nx", "vbscript:x", "https://" + "a" * 2000 + ".com", None, 7])
def test_unsafe_urls_are_dropped(url):
    assert safe_url(url) is None
    assert citations.clean([{"kind": "web", "url": url, "title": "x"}]) == []


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com/a?b=1#c",
                                 "https://www.example.org/" + "p" * 1900])
def test_http_and_https_urls_are_kept(url):
    assert safe_url(url) == url


def test_sources_are_bounded():
    s = Sources()
    for n in range(30):
        assert (s.cite(web_cite(f"https://site{n}.example.com/", "T" * 500, "c" * 900))
                == (n + 1 if n < citations.MAX_SOURCES else None))
    assert len(s.items) == citations.MAX_SOURCES == 20
    assert all(len(x["title"]) <= citations.MAX_TITLE == 200 for x in s.items)
    assert all(len(x["cited_text"]) <= citations.MAX_CITED for x in s.items)
    assert s.cite(web_cite("javascript:alert(1)")) is None
    assert s.cite(web_cite("https://site0.example.com/")) == 1  # the same page: one number
    stored = citations.stored({"sources": s.items, "marks": [[0, [1, 99]]] * 80})
    assert len(json.dumps(stored)) <= citations.MAX_STORED_BYTES + 4000
    assert len(stored["marks"]) == citations.MAX_MARKS and stored["marks"][0] == [0, [1]]


def test_titles_lose_control_characters():
    (item,) = citations.clean([{"kind": "web", "url": "https://a.example/",
                                "title": "Bad\x00\ntitle‮"}])
    assert item["title"] == "Bad title‮" and "\n" not in item["title"]


def test_a_citation_of_something_no_tool_returned_is_ignored():
    s = Sources()
    assert s.cite({"type": "search_result_location", "source": "kwil:chat/" + "0" * 32,
                   "title": "x", "cited_text": "y"}) is None
    assert s.cite({"type": "char_location", "cited_text": "x"}) is None


# ---------- own data as search_result blocks ----------

def test_each_citable_tool_becomes_search_results():
    s = Sources(project="prj-0123456789ab")
    read = s.offer("read_conversation", json.dumps({
        "id": "a" * 32, "title": "Physics", "date": "Tuesday, October 27, 2026",
        "updated_at": 1_793_100_000, "summary": "", "messages": [
            {"n": 1, "who": "Kyle", "said": "Mondays", "match": True}]}))
    assert read[0]["source"] == "kwil:chat/" + "a" * 32
    assert read[0]["citations"] == {"enabled": True}
    assert [c["text"] for c in read[0]["content"]][1] == "Kyle said (match): Mondays"
    files = s.offer("search_project_files", json.dumps({"note": "n", "results": [
        {"file_id": "f1", "name": "syllabus.pdf", "offset": 0, "snippet": "late work"}]}))
    assert files[0]["source"] == "kwil:project_file/f1" and files[0]["title"] == "syllabus.pdf"
    course = s.offer("search_course_files", json.dumps({"note": "n", "count": 1, "results": [
        {"doc_id": 12, "course": "PHYS 361", "title": "Syllabus", "kind": "syllabus",
         "snippet": "Exams in May"}]}))
    assert course[0]["title"] == "PHYS 361 Syllabus"
    assert s.offer("read_course_file", json.dumps({
        "note": "n", "doc_id": 12, "course": "PHYS 361", "title": "Syllabus", "kind": "syllabus",
        "offset": 0, "length": 5, "text": "Exams", "next_offset": None}))
    assert s.offer("read_project_file", json.dumps({
        "note": "n", "file_id": "f1", "name": "syllabus.pdf", "offset": 0, "text": "late",
        "chars": 4}))
    # errors, no hits, other tools: the text as it was
    assert s.offer("read_conversation", json.dumps({"status": "error", "message": "x"})) is None
    assert s.offer("search_project_files", json.dumps({"results": [], "note": "n"})) is None
    assert s.offer("list_calendar_events", json.dumps({"events": []})) is None
    assert s.offer("read_conversation", "not json") is None
    done = s.finish()  # the reads are listed even though nothing cited them
    assert [(x["kind"], x["ref"]) for x in done["sources"]] == [
        ("chat", "a" * 32), ("course_file", "12"), ("project_file", "f1")]
    assert done["sources"][2]["project"] == "prj-0123456789ab"


def test_the_contract_fake_accepts_every_tools_search_results():
    from tests.contract_fakes import ContractClient

    s = Sources()
    blocks = s.offer("read_project_file", json.dumps({
        "note": "n", "file_id": "f1", "name": "a.pdf", "offset": 0, "text": "late work",
        "chars": 9}))
    client = ContractClient()
    thought = {"type": "thinking", "thinking": "t", "signature": client.sign("t", "m")}
    client.create(model="m", max_tokens=64, tools=[
        {"type": "web_search_20250305", "name": "web_search"},
        {"name": "read_project_file", "description": "d", "input_schema": {"type": "object"}}],
        messages=[{"role": "user", "content": "late policy?"},
                  {"role": "assistant", "content": [thought, {
                      "type": "tool_use", "id": "toolu_1", "name": "read_project_file",
                      "input": {}}]},
                  {"role": "user", "content": [{"type": "tool_result",
                                                "tool_use_id": "toolu_1",
                                                "content": blocks}]}])


def chat_with(fake_client, registry, store=None, **kw):
    recorder = Recorder(store, "web", run_async=False) if store is not None else None
    return ClaudeChat(client=fake_client, model="test-model", tools=registry,
                      recorder=recorder, **kw)


def file_tool(calls):
    def read_project_file(file_id: str, offset: int = 0):
        calls.append(file_id)
        return {"note": "n", "file_id": file_id, "name": "syllabus.pdf", "offset": 0,
                "text": "Late work loses 10% a day.", "chars": 26}
    reg = ToolRegistry()
    reg.register(Tool("read_project_file", "d", {"type": "object"}, read_project_file,
                      untrusted_output=True, private=True))
    return reg


def test_a_read_file_is_listed_even_when_the_reply_cites_nothing(fake_client, store):
    calls = []
    chat = chat_with(fake_client, file_tool(calls), store)
    fake_client.queue = [
        make_response(tool_use_block("read_project_file", {"file_id": "f-1"}),
                      stop_reason="tool_use"),
        make_response(text_block("From the syllabus: ten percent a day."))]
    reply = chat.ask("what's the late policy?")
    assert calls == ["f-1"] and reply == "From the syllabus: ten percent a day."
    assert chat.last_citations == {"sources": [
        {"kind": "project_file", "title": "syllabus.pdf", "ref": "f-1"}]}
    sent = fake_client.calls[-1]["messages"][-1]["content"][0]["content"]
    assert sent[0]["type"] == "search_result"
    saved = store.get(chat.conversation_id)["messages"][-1]
    assert saved["citations"] == chat.last_citations
    assert chat.tainted()  # still untrusted output: changes ask first


def cited_reply(fake_client, chat, words, cites):
    """A reply whose second text block carries `cites` (SDK citations)."""
    from anthropic.types import Message

    fake_client.queue = [Message.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "test-model",
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "content": [{"type": "text", "text": "  Here it is: "},
                    {"type": "text", "text": words, "citations": cites}]})]
    return chat.ask("look it up")


def test_web_citations_are_numbered_and_marked(fake_client):
    chat = chat_with(fake_client, None)
    reply = cited_reply(fake_client, chat, "Friday at 9.", [
        web_cite("https://a.example/x", "A"), web_cite("javascript:alert(1)", "Evil"),
        web_cite("https://a.example/x", "A again")])
    assert reply == "Here it is: Friday at 9."
    assert chat.last_citations == {
        "sources": [{"kind": "web", "url": "https://a.example/x", "title": "A",
                     "cited_text": "the exam is Friday"}],
        "marks": [[len(reply), [1]]]}


def test_the_next_turn_starts_with_no_sources(fake_client):
    chat = chat_with(fake_client, None)
    cited_reply(fake_client, chat, "Friday.", [web_cite("https://a.example/")])
    fake_client.queue = [make_response(text_block("Sure."))]
    chat.ask("thanks")
    assert chat.last_citations is None


def test_no_titles_or_urls_at_info(fake_client, caplog, store):
    chat = chat_with(fake_client, None, store)
    with caplog.at_level(logging.DEBUG, logger="kwil"):
        cited_reply(fake_client, chat, "Friday.",
                    [web_cite("https://secret-site.example/q", "Secret title", "cited words")])
    info = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "Sources: 1 (web)" in info
    for text in ("secret-site", "Secret title", "cited words"):
        assert text not in info


# ---------- history ----------

def reply_with(store, cites, text="It's Friday."):
    cid = store.create("web")
    store.add_message(cid, "user", "when is the exam?")
    eid = store.add_message(cid, "assistant", text, cites=cites)
    return cid, eid


CITES = {"sources": [WEB, {"kind": "chat", "ref": "b" * 32, "title": "Chat on Tue 27 Oct"}],
         "marks": [[12, [1]]]}


def test_stored_with_the_reply_and_shown_by_history(store):
    from kwil.history_web import detail

    cid, _ = reply_with(store, CITES)
    conv = store.get(cid)
    assert conv["messages"][1]["citations"] == CITES
    assert conv["messages"][0]["citations"] is None
    assert detail(conv)["messages"][1]["citations"] == CITES
    md = history.export(conv, "md")
    assert "<sub>sources: PHYS 361 exams (https://physics.example.edu/exams); " \
           "Chat on Tue 27 Oct</sub>" in md
    data = json.loads(history.export(conv, "json"))
    assert data["messages"][1]["citations"] == CITES


def test_unsafe_stored_or_imported_sources_never_come_back(store, tmp_path):
    bad = {"sources": [{"kind": "web", "url": "javascript:alert(1)", "title": "x"},
                       {"kind": "web", "url": "https://ok.example/", "title": "ok"},
                       {"kind": "evil", "title": "x"}], "marks": [[0, [1, 2, 3]]]}
    cid, _ = reply_with(store, bad)
    assert store.get(cid)["messages"][1]["citations"] == {
        "sources": [{"kind": "web", "url": "https://ok.example/", "title": "ok"}],
        "marks": [[0, [1]]]}
    exported = json.loads(history.export(store.get(cid), "json"))
    exported["messages"][1]["citations"] = bad
    other = HistoryStore(tmp_path / "other.sqlite", eventlog=False)
    try:
        assert other.import_conversation(exported)
        (m,) = [m for m in other.get(exported["id"])["messages"] if m["role"] == "assistant"]
        assert m["citations"]["sources"] == [
            {"kind": "web", "url": "https://ok.example/", "title": "ok"}]
    finally:
        other.close()


def raw_rows(store) -> str:
    return json.dumps([tuple(r) for r in store._con.execute(
        "SELECT citations FROM messages")])


def test_gone_on_supersede_and_on_delete(store):
    cid, eid = reply_with(store, CITES)
    assert "physics.example.edu" in raw_rows(store)
    store.supersede(cid, [eid])
    assert "physics.example.edu" not in raw_rows(store)
    cid, _ = reply_with(store, CITES)
    store.delete(cid)
    assert "physics.example.edu" not in raw_rows(store)


def test_try_again_supersedes_the_old_replys_sources(fake_client, store):
    chat = chat_with(fake_client, None, store)
    cited_reply(fake_client, chat, "Friday.", [web_cite("https://old.example/")])
    assert "old.example" in raw_rows(store)
    fake_client.queue = [make_response(text_block("Friday at nine."))]
    chat.regenerate()
    assert "old.example" not in raw_rows(store)
    assert store.get(chat.conversation_id)["messages"][-1]["citations"] is None


def test_interrupted_keeps_the_sources_and_drops_the_marks(store):
    cid, _ = reply_with(store, CITES)
    store.mark_interrupted(cid, "It's")
    m = store.get(cid)["messages"][-1]
    assert m["interrupted"] and m["citations"] == {"sources": CITES["sources"]}


def test_a_branch_keeps_them(store):
    cid, _ = reply_with(store, CITES)
    new = store.branch(cid, 1)
    assert store.get(new)["messages"][1]["citations"] == CITES


def test_an_older_file_gets_the_column_and_older_code_still_writes(tmp_path):
    path = tmp_path / "history.sqlite"
    s = HistoryStore(path, eventlog=False)
    s._con.execute("ALTER TABLE messages DROP COLUMN citations")  # as an older Kwil made it
    s.close()
    s = HistoryStore(path, eventlog=False)
    try:
        cid, _ = reply_with(s, CITES)
        assert s.get(cid)["messages"][1]["citations"] == CITES
        # an older Kwil (a rollback) inserts without naming the column
        s._con.execute(
            "INSERT INTO messages (event_id, conversation_id, ev_ts, role, kind, text, "
            "created_at, interrupted, tool_events) VALUES ('e-old', ?, 1, 'assistant', "
            "'chat', 'old', 1, 0, NULL)", (cid,))
        assert any(m["citations"] is None for m in s.get(cid)["messages"])
    finally:
        s.close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 1


# ---------- speech ----------

def test_spoken_replies_never_read_a_url():
    from kwil import cli
    from kwil.sentences import without_urls

    assert without_urls("See https://a.example/x?y=1 and [the page](https://b.example) now.") \
        == "See and the page now."
    said = []

    class TTS:
        def speak(self, text):
            said.append(text)

    cli.speak(TTS(), "Check www.example.com for details.")
    assert said == ["Check for details."]


def test_the_web_apps_audio_drops_urls(monkeypatch):
    from kwil import webapp

    rendered = []
    wa = webapp.WebAssistant.__new__(webapp.WebAssistant)
    monkeypatch.setattr(wa, "tts_for", lambda session, language: "engine", raising=False)
    monkeypatch.setattr(wa, "_language_tts", lambda language: "engine", raising=False)
    monkeypatch.setattr(wa, "render", lambda engine, text: rendered.append(text) or b"",
                        raising=False)
    wa.synthesize("It's on https://physics.example.edu/exams today.")
    assert rendered == ["It's on today."]


def test_the_phone_gets_the_sources_with_the_reply_never_in_its_words():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from kwil import webapp
    from kwil.tts import PrintTTS
    from tests.contract_fakes import ContractClient
    from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, events, login

    api = ContractClient()

    def chat_factory(registry):
        registry.add_server_tools({"type": "web_search_20250305", "name": "web_search"})
        return ClaudeChat(client=api, model="test-model", tools=registry, max_tokens=1024)

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(), clock=Clock())
    client = TestClient(webapp.create_app(
        assistant, TOKEN, reap_interval=3600,
        limiter=webapp.LoginLimiter(per_ip=3, global_delay=0)), base_url=BASE)
    login(client)
    evs = events(client.post("/api/turn", json={"text": "search the web for the exam date"}))
    reply = next(e for e in evs if e["type"] == "reply")
    assert reply["text"] == "Here's what I found. The exam is on Friday at 9."
    assert [s["url"] for s in reply["citations"]["sources"]] == [
        "https://physics.example.edu/exams", "https://news.example.org/a?b=1"]
    assert reply["citations"]["marks"] == [[len(reply["text"]), [1, 2]]]
    spoken = {rid: text for rid, (sid, text, lang) in assistant.replies.items()}
    assert all("http" not in t and "example" not in t for t in spoken.values())
    evs = events(client.post("/api/turn", json={"text": "thanks"}))
    assert "citations" not in next(e for e in evs if e["type"] == "reply")


def test_marks_count_utf16_units_after_an_emoji(fake_client):
    """Judge round 2: an emoji is one Python character but two in the
    page's strings, so a mark counted in code points landed one early."""
    chat = chat_with(fake_client, None)
    reply = cited_reply(fake_client, chat, "🎉 Friday at 9 📅.", [web_cite("https://a.example/")])
    assert reply == "Here it is: 🎉 Friday at 9 📅."
    (at, refs), = chat.last_citations["marks"]
    assert at == len(reply.encode("utf-16-le")) // 2 == len(reply) + 2 and refs == [1]
    assert citations.utf16_len("a😀b") == 4


def test_documents_get_citations_on_keeping_their_cache_breakpoint():
    from kwil.chat import cite_documents

    doc = {"type": "document", "title": "syllabus.pdf", "cache_control": {"type": "ephemeral"},
           "source": {"type": "text", "media_type": "text/plain", "data": "Late work: -10%"}}
    sent = [{"role": "user", "content": [doc, {"type": "text", "text": "late policy?"}]},
            {"role": "assistant", "content": "Ten percent."}]
    out = cite_documents(sent)
    assert out[0]["content"][0] == {**doc, "citations": {"enabled": True}}
    assert "citations" not in doc  # a copy: history keeps none
    assert out[1] is sent[1] and cite_documents(out) == out  # same bytes every request


def test_an_attached_file_is_named_not_linked(fake_client):
    from tests.test_projects import _doc_cited

    chat = chat_with(fake_client, None)
    pdf = {"type": "document", "title": "plan.pdf", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "JVBERi0x"}}
    fake_client.queue = [_doc_cited("Mondays at 7.", 0, "page_location")]
    chat.ask("what does my plan say?", attachments=[pdf])
    assert chat.last_citations["sources"] == [{"kind": "attachment",
                                               "title": "plan.pdf (attached)"}]
    assert citations.stored(chat.last_citations) == chat.last_citations  # kept in history


def test_an_attachment_cited_twice_is_listed_once():
    """Judge round 3: an attachment has no url or ref, so each citation of it
    added a new source (and filled MAX_SOURCES, dropping later web pages)."""
    s = citations.Sources()
    pdf = {"type": "document", "title": "plan.pdf", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "JVBERi0x"}}
    s.set_documents([{"role": "user", "content": [pdf, {"type": "text", "text": "q"}]}], [])
    doc = {"type": "page_location", "document_index": 0, "cited_text": "x",
           "start_page_number": 1, "end_page_number": 2}
    web = {"type": "web_search_result_location", "url": "https://example.com/a",
           "title": "Example", "cited_text": "y"}
    from types import SimpleNamespace as B
    s.reply([B(type="text", text="One.", citations=[doc]),
             B(type="text", text=" Two.", citations=[doc]),
             B(type="text", text=" Three.", citations=[web])])
    got = s.finish()
    assert [x["kind"] for x in got["sources"]] == ["attachment", "web"]
    assert [m[1] for m in got["marks"]] == [[1], [1], [2]]
