"""Lecture recorder backend: resumable chunk uploads, the transcribe ->
summarize -> Notion pipeline with mocks, and finding/creating the Lecture
Notes database."""

import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from assistant.lecture import (AUDIO_FILE, ChunkedUploads, LectureNotesDB,
                               LecturePipeline, NotionBlocked, UploadGap,
                               UploadNotFound, UploadTooLarge,
                               assignment_candidates, notes_blocks, rich_text,
                               summarize)
from assistant.tools.notion import NotionAssignments
from tests.conftest import make_response, tool_use_block

TRANSCRIPT_WORDS = ["Today", "we", "cover", "kinetic", "energy", "SECRET-LECTURE"]


# ---------- chunked uploads ----------
@pytest.fixture
def uploads(tmp_path):
    return ChunkedUploads(tmp_path / "lectures", max_bytes=1000, max_chunk_bytes=100)


def test_chunks_append_in_order(uploads):
    uid = uploads.start(course="PHYS 141", mime="audio/webm",
                        started_at="2026-10-16T09:00:00-04:00")
    assert uploads.append(uid, 0, b"a" * 100) == 100
    assert uploads.append(uid, 100, b"b" * 50) == 150
    meta = uploads.finish(uid, total=150)
    assert meta["finished"] and meta["course"] == "PHYS 141"
    assert uploads.audio_path(uid).read_bytes() == b"a" * 100 + b"b" * 50


def test_resume_after_a_network_blip(uploads):
    """Chunk 2 is lost in transit, chunk 3 arrives early: the server says
    where it is, the phone resends from there, and a repeat is harmless."""
    uploads.max_chunk_bytes = 200
    uid = uploads.start()
    chunks = [bytes([i]) * 100 for i in range(5)]
    uploads.append(uid, 0, chunks[0])
    uploads.append(uid, 100, chunks[1])
    # chunk 2 (offset 200) lost; chunk 3 arrives
    with pytest.raises(UploadGap) as gap:
        uploads.append(uid, 300, chunks[3])
    assert gap.value.received == 200 and gap.value.status == 409
    # the phone reconnects and asks
    assert uploads.status(uid)["received"] == 200
    # the response to chunk 1 was lost, so it is sent again: no duplicate
    assert uploads.append(uid, 100, chunks[1]) == 200
    # a resend overlapping the end only adds the new part
    assert uploads.append(uid, 150, chunks[1][:50] + chunks[2]) == 300
    for i in (3, 4):
        uploads.append(uid, i * 100, chunks[i])
    with pytest.raises(UploadGap):
        uploads.finish(uid, total=600)  # phone thinks it sent more
    uploads.finish(uid, total=500)
    assert uploads.audio_path(uid).read_bytes() == b"".join(chunks)


def test_size_caps(uploads):
    uid = uploads.start()
    with pytest.raises(UploadTooLarge):
        uploads.append(uid, 0, b"x" * 101)  # chunk cap
    for i in range(10):
        uploads.append(uid, i * 100, b"x" * 100)
    with pytest.raises(UploadTooLarge) as err:
        uploads.append(uid, 1000, b"x")  # recording cap
    assert err.value.status == 413
    assert uploads.status(uid)["received"] == 1000


def test_bad_ids_and_finished_uploads(uploads):
    for bad in ["../../etc", "", "x", "a" * 100, "nope-nope-nope-nope"]:
        with pytest.raises(UploadNotFound):
            uploads.status(bad)
    uid = uploads.start()
    with pytest.raises(Exception, match="Nothing was uploaded"):
        uploads.finish(uid)
    uploads.append(uid, 0, b"x")
    uploads.finish(uid)
    with pytest.raises(Exception, match="already finished"):
        uploads.append(uid, 1, b"y")


def test_concurrent_resends_never_corrupt(uploads):
    uploads.max_bytes = 100_000
    uid = uploads.start()
    data = bytes(range(256)) * 40  # 10240 bytes
    pieces = [(o, data[o:o + 100]) for o in range(0, len(data), 100)]

    def sender():
        for offset, chunk in pieces:
            for _ in range(50):
                try:
                    uploads.append(uid, offset, chunk)
                    break
                except UploadGap:
                    time.sleep(0.001)

    threads = [threading.Thread(target=sender) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert uploads.audio_path(uid).read_bytes() == data


# ---------- summary ----------
NOTES = {
    "title": "Work and kinetic energy",
    "summary": "Work done equals the change in kinetic energy, $W = \\Delta K$.",
    "key_concepts": ["Work-energy theorem", "Kinetic energy $K = \\frac{1}{2}mv^2$"],
    "equations": [{"latex": "K = \\frac{1}{2} m v^2", "meaning": "Kinetic energy"},
                  {"latex": "$W = F d \\cos\\theta$"}],
    "action_items": [
        {"text": "Problem set 5", "due": "2026-10-23", "is_assignment": True,
         "type": "Homework"},
        {"text": "Read chapter 7", "due": "", "is_assignment": False},
        {"text": "Lab report 4", "due": "next week", "is_assignment": True}],
    "questions": ["Why is work a scalar?"],
}


def test_summarize_forces_the_notes_tool(fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    out = summarize(fake_client, "m", "the transcript", "PHYS 141", "2026-10-16")
    call = fake_client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "save_lecture_notes"}
    assert "<transcript>\nthe transcript\n</transcript>" in call["messages"][0]["content"]
    assert "Course: PHYS 141" in call["messages"][0]["content"]
    assert out["title"] == "Work and kinetic energy"


def test_only_assignment_like_items_are_offered():
    cands = assignment_candidates(NOTES, "PHYS 141")
    assert [c["name"] for c in cands] == ["Problem set 5", "Lab report 4"]
    assert cands[0] == {"index": 0, "name": "Problem set 5", "course": "PHYS 141",
                        "due": "2026-10-23", "type": "Homework"}
    assert cands[1]["due"] == ""  # "next week" isn't a date


def test_notion_blocks_have_every_section_and_equations():
    blocks = notes_blocks(NOTES)
    headings = [b["heading_2"]["rich_text"][0]["text"]["content"]
                for b in blocks if b["type"] == "heading_2"]
    assert headings == ["Summary", "Key concepts", "Equations", "Action items",
                        "Questions to ask"]
    equations = [b["equation"]["expression"] for b in blocks if b["type"] == "equation"]
    assert equations == ["K = \\frac{1}{2} m v^2", "W = F d \\cos\\theta"]
    todos = [b for b in blocks if b["type"] == "to_do"]
    assert len(todos) == 3 and "(due 2026-10-23)" in \
        todos[0]["to_do"]["rich_text"][0]["text"]["content"]
    # inline maths in text becomes an inline equation
    summary = blocks[1]["paragraph"]["rich_text"]
    assert summary[1] == {"type": "equation", "equation": {"expression": "W = \\Delta K"}}


def test_rich_text_splits_long_text():
    parts = rich_text("x" * 5000)
    assert [len(p["text"]["content"]) for p in parts] == [1900, 1900, 1200]


# ---------- Notion database ----------
def assignments_mock(parent=("page_id", "fall-page"), options=None):
    client = MagicMock()
    client.databases.retrieve.return_value = {
        "data_sources": [{"id": "ds-assign"}],
        "parent": {"type": parent[0], parent[0]: parent[1]}}
    client.data_sources.retrieve.return_value = {"properties": {"Course": {
        "select": {"options": options or [{"name": "PHYS 141", "color": "blue"},
                                          {"name": "CS II", "color": "red"}]}}}}
    client.search.return_value = {"results": []}
    client.databases.create.return_value = {"id": "db-notes",
                                            "data_sources": [{"id": "ds-notes"}]}
    client.pages.create.side_effect = lambda **kw: {
        "id": "page1", "url": "https://notion.so/page1", **kw}
    return NotionAssignments("db-assign", client=client), client


def test_creates_lecture_notes_database_when_missing(tmp_path):
    assignments, client = assignments_mock()
    db = LectureNotesDB(assignments, state_file=tmp_path / "state.json")
    assert db.data_source_id() == "ds-notes"
    kw = client.databases.create.call_args.kwargs
    assert kw["parent"] == {"type": "page_id", "page_id": "fall-page"}
    assert kw["title"][0]["text"]["content"] == "Lecture Notes"
    props = kw["initial_data_source"]["properties"]
    assert set(props) == {"Title", "Course", "Date", "Duration", "Lecture ID"}
    assert props["Title"] == {"title": {}} and props["Date"] == {"date": {}}
    assert props["Course"]["select"]["options"] == [
        {"name": "PHYS 141", "color": "blue"}, {"name": "CS II", "color": "red"}]
    assert "number" in props["Duration"]
    # remembered: a second instance neither searches nor creates
    client.reset_mock()
    again = LectureNotesDB(assignments, state_file=tmp_path / "state.json")
    assert again.data_source_id() == "ds-notes"
    assert not client.search.called and not client.databases.create.called


def test_finds_an_existing_lecture_notes_database(tmp_path):
    assignments, client = assignments_mock()
    client.search.return_value = {"results": [
        {"object": "data_source", "id": "ds-elsewhere",
         "title": [{"plain_text": "Lecture Notes"}],
         "parent": {"database_id": "db-x"},
         "database_parent": {"type": "page_id", "page_id": "other-page"}},
        {"object": "data_source", "id": "ds-old",
         "title": [{"plain_text": "Lecture Notes (old)"}],
         "parent": {"database_id": "db-y"}},
        {"object": "data_source", "id": "ds-right",
         "title": [{"plain_text": "Lecture Notes"}],
         "parent": {"database_id": "db-right"},
         "database_parent": {"type": "page_id", "page_id": "fall-page"}}]}
    db = LectureNotesDB(assignments)
    assert db.data_source_id() == "ds-right" and db.database_id == "db-right"
    assert not client.databases.create.called
    assert client.search.call_args.kwargs["filter"] == {
        "property": "object", "value": "data_source"}


def test_configured_database_id_is_used(tmp_path):
    assignments, client = assignments_mock()
    client.databases.retrieve.side_effect = lambda database_id: (
        {"data_sources": [{"id": "ds-configured"}]} if database_id == "db-cfg"
        else {"data_sources": [{"id": "ds-assign"}], "parent": {}})
    db = LectureNotesDB(assignments, database_id="db-cfg")
    assert db.data_source_id() == "ds-configured"
    assert not client.search.called


def test_creation_refused_explains_what_kyle_must_do():
    from notion_client import APIResponseError

    assignments, client = assignments_mock()
    # Built by hand: the constructor's signature differs between versions.
    err = APIResponseError.__new__(APIResponseError)
    Exception.__init__(err, "Insufficient permissions")
    err.code, err.status = "restricted_resource", 403
    client.databases.create.side_effect = err
    with pytest.raises(NotionBlocked, match="Connections") as e:
        LectureNotesDB(assignments).data_source_id()
    assert "notes_database_id" in str(e.value)

    assignments, client = assignments_mock(parent=("workspace", True))
    with pytest.raises(NotionBlocked, match="isn't inside a page"):
        LectureNotesDB(assignments).data_source_id()


def test_page_has_properties_and_course_link():
    assignments, client = assignments_mock()
    db = LectureNotesDB(assignments)
    page = db.create_page(NOTES, "PHYS 141", "2026-10-16", 76.4)
    kw = client.pages.create.call_args.kwargs
    assert kw["parent"] == {"type": "data_source_id", "data_source_id": "ds-notes"}
    props = kw["properties"]
    assert props["Title"]["title"][0]["text"]["content"] == "Work and kinetic energy"
    assert props["Course"] == {"select": {"name": "PHYS 141"}}
    assert props["Date"] == {"date": {"start": "2026-10-16"}}
    assert props["Duration"] == {"number": 76}
    assert page["url"] == "https://notion.so/page1"


def test_long_notes_are_appended_in_batches():
    assignments, client = assignments_mock()
    many = {**NOTES, "key_concepts": [f"c{i}" for i in range(250)]}
    LectureNotesDB(assignments).create_page(many, "", "2026-10-16", None)
    assert len(client.pages.create.call_args.kwargs["children"]) == 100
    assert client.blocks.children.append.call_count == 2
    assert "Course" not in client.pages.create.call_args.kwargs["properties"]


# ---------- the pipeline ----------
class Samples:
    """Stands in for a window of audio: only its length matters here."""

    def __init__(self, seconds):
        self.n = int(seconds * 16000)

    def __len__(self):
        return self.n


def fake_windows(duration=4560.0, window=600.0):
    """(samples, start, fraction) like audio_windows, for a long lecture."""
    out, start = [], 0.0
    while start < duration:
        length = min(window, duration - start)
        out.append((Samples(length), start, min(1.0, (start + length) / duration)))
        start += window
    return out


class FakeWhisper:
    """Returns the transcript words spread over the windows it is given."""

    def __init__(self, words=TRANSCRIPT_WORDS):
        self.words = list(words)
        self.calls = []

    def transcribe(self, samples, **kw):
        self.calls.append(kw)
        word = self.words.pop(0) if self.words else ""
        seconds = len(samples) / 16000
        return iter([SimpleNamespace(text=f" {word}", end=seconds / 2),
                     SimpleNamespace(text="", end=seconds)]), None


def pipeline(tmp_path, fake_client, keep_audio=False, whisper=None):
    up = ChunkedUploads(tmp_path / "lec")
    assignments, notion = assignments_mock()
    whisper = whisper or FakeWhisper()
    p = LecturePipeline(up, fake_client, LectureNotesDB(assignments), model="m",
                        whisper_model="tiny.en", keep_audio=keep_audio,
                        whisper_factory=lambda name: whisper)
    p._windows = lambda audio: iter(fake_windows())
    uid = up.start(course="PHYS 141", started_at="2026-10-16T09:00:00-04:00")
    up.append(uid, 0, b"fake-audio")
    up.finish(uid)
    return p, up, uid, notion, whisper, assignments


def test_pipeline_end_to_end_with_mocks(tmp_path, fake_client, caplog):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, notion, whisper, _ = pipeline(tmp_path, fake_client)
    p.submit(uid)
    with caplog.at_level(logging.DEBUG):
        for _ in range(200):
            if p.job(uid).status in ("done", "failed"):
                break
            time.sleep(0.02)
    job = p.job(uid)
    assert job.status == "done", job.error
    assert job.notion_url == "https://notion.so/page1"
    assert job.duration_seconds == 4560.0 and job.progress == 1.0
    assert job.title == "Work and kinetic energy"
    assert [c["name"] for c in job.candidates] == ["Problem set 5", "Lab report 4"]
    assert whisper.calls[0]["vad_filter"] is True and len(whisper.calls) == 8
    assert "SECRET-LECTURE" in fake_client.calls[0]["messages"][0]["content"]
    assert notion.pages.create.call_args.kwargs["properties"]["Duration"] == {
        "number": 76}
    d = up.directory(uid)
    assert not (d / AUDIO_FILE).exists()           # audio deleted
    assert not (d / "transcript.txt").exists()     # transcript deleted
    assert "SECRET-LECTURE" not in caplog.text     # never logged
    assert not notion.pages.create.call_args.kwargs["parent"].get("database_id")
    # nothing was added to Assignments on its own
    assert all(c.kwargs["parent"]["data_source_id"] != "ds-assign"
               for c in notion.pages.create.call_args_list)


def test_keep_audio_setting(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, _, _, _ = pipeline(tmp_path, fake_client, keep_audio=True)
    p.submit(uid)
    p.wait(10)
    assert p.job(uid).status == "done"
    assert (up.directory(uid) / AUDIO_FILE).read_bytes() == b"fake-audio"


def test_progress_is_reported(tmp_path, fake_client):
    seen = []
    p, up, uid, _, whisper, _ = pipeline(tmp_path, fake_client)
    from assistant.lecture import LectureJob
    p._save(LectureJob(id=uid))
    p._save = lambda job: seen.append((job.status, round(job.progress, 2)))
    import assistant.lecture as lecture
    real = lecture.time.monotonic
    ticks = iter(range(0, 1000, 3))
    lecture.time.monotonic = lambda: next(ticks)  # every segment saves
    try:
        job = p.job(uid)
        p._transcribe(job, up.directory(uid), up.directory(uid) / "transcript.txt")
    finally:
        lecture.time.monotonic = real
    progress = [pr for st, pr in seen if st == "transcribing"]
    assert progress[0] == 0.0 and progress[-1] == 1.0
    assert progress == sorted(progress) and len(set(progress)) >= 8


def test_notion_failure_keeps_transcript_and_retry_resumes(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, notion, whisper, _ = pipeline(tmp_path, fake_client)
    notion.pages.create.side_effect = RuntimeError("Notion is down")
    p.submit(uid)
    p.wait(10)
    job = p.job(uid)
    assert job.status == "failed" and "Notion is down" in job.error
    d = up.directory(uid)
    assert (d / "transcript.txt").exists() and (d / "notes.json").exists()
    # retry: no second transcription or summary, just the Notion step
    notion.pages.create.side_effect = lambda **kw: {"id": "p2", "url": "u2"}
    p.submit(uid)
    p.wait(10)
    assert p.job(uid).status == "done" and p.job(uid).notion_url == "u2"
    assert len(whisper.calls) == 8 and len(fake_client.calls) == 1  # no re-run


def test_silent_recording_fails_clearly(tmp_path, fake_client):
    p, up, uid, _, _, _ = pipeline(tmp_path, fake_client,
                                   whisper=FakeWhisper(words=[]))
    p.submit(uid)
    p.wait(10)
    assert p.job(uid).status == "failed"
    assert "No speech" in p.job(uid).error


def test_unfinished_upload_cant_be_submitted(tmp_path, fake_client):
    p, up, _, _, _, _ = pipeline(tmp_path, fake_client)
    uid = up.start()
    with pytest.raises(Exception, match="Finish the upload"):
        p.submit(uid)


def test_resume_pending_after_restart(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, _, _, assignments = pipeline(tmp_path, fake_client)
    from assistant.lecture import LectureJob
    p._save(LectureJob(id=uid, course="PHYS 141",
                       started_at="2026-10-16T09:00:00-04:00",
                       status="transcribing", progress=0.4))
    assert p.resume_pending() == 1
    p.wait(10)
    assert p.job(uid).status == "done"


def test_adding_an_offered_assignment_only_on_request(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, notion, _, assignments = pipeline(tmp_path, fake_client)
    p.submit(uid)
    p.wait(10)
    notion.pages.create.side_effect = lambda **kw: {
        "id": "a1", "properties": {
            "Assignment": {"title": [{"plain_text": "Problem set 5"}]},
            "Status": {"status": {"name": "Not started"}}}}
    out = p.add_candidate(uid, 0, assignments, {"due": "2026-10-24"})
    kw = notion.pages.create.call_args.kwargs
    assert kw["parent"] == {"type": "data_source_id", "data_source_id": "ds-assign"}
    props = kw["properties"]
    assert props["Assignment"]["title"][0]["text"]["content"] == "Problem set 5"
    assert props["Status"] == {"status": {"name": "Not started"}}
    assert props["Course"] == {"select": {"name": "PHYS 141"}}
    assert props["Due"] == {"date": {"start": "2026-10-24"}}
    assert props["Type"] == {"select": {"name": "Homework"}}
    assert out["status"] == "created"
    assert p.add_candidate(uid, 0, assignments)["status"] == "already_added"
    with pytest.raises(Exception, match="No such suggestion"):
        p.add_candidate(uid, 1, assignments)  # "Read chapter 7" isn't offered


def test_course_options_from_assignments():
    assignments, _ = assignments_mock()
    assert [o["name"] for o in assignments.course_options()] == ["PHYS 141", "CS II"]
    assert assignments.parent_page_id() == "fall-page"


# ---------- real decoding of phone recordings ----------
FIXTURE = str(Path(__file__).parent / "fixtures" / "hey_claude_sapi_david_140.wav")


def encode(src, dst, fmt, codec):
    """Re-encode a .wav the way a phone records: WebM/Opus or MP4/AAC."""
    import av

    with av.open(src) as inp, av.open(str(dst), "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=48000)
        for frame in inp.decode(audio=0):
            frame.pts = None
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


@pytest.mark.parametrize("fmt,codec", [("webm", "libopus"), ("mp4", "aac"),
                                       (None, None)])
def test_audio_windows_decodes_phone_formats(tmp_path, fmt, codec):
    import numpy as np
    from assistant.audio import load_audio
    from assistant.lecture import audio_windows

    path = tmp_path / AUDIO_FILE  # no extension, as stored
    if fmt is None:
        path.write_bytes(Path(FIXTURE).read_bytes())
    else:
        try:
            encode(FIXTURE, path, fmt, codec)
        except Exception as e:  # codec missing from this PyAV build
            pytest.skip(f"{codec} unavailable: {type(e).__name__}")
    expected = len(load_audio(FIXTURE)) / 16000
    windows = list(audio_windows(path, window_seconds=0.5))
    assert len(windows) >= 2
    starts = [w[1] for w in windows]
    assert starts == [i * 0.5 for i in range(len(windows))]
    total = sum(len(w[0]) for w in windows) / 16000
    assert abs(total - expected) < 0.15
    assert all(w[0].dtype == np.float32 and np.abs(w[0]).max() <= 1.0
               for w in windows)
    assert windows[-1][2] == 1.0
    fractions = [w[2] for w in windows]
    assert fractions == sorted(fractions)


@pytest.mark.slow
def test_pipeline_transcribes_a_real_webm(tmp_path, fake_client):
    """Generated speech -> WebM/Opus -> the real Whisper model."""
    from assistant.stt import Transcriber, rough_match
    from assistant.tts import Pyttsx3TTS

    phrase = "Today we cover kinetic energy and the work energy theorem"
    wav = tmp_path / "lecture.wav"
    Pyttsx3TTS(rate=160).save_to_file(phrase, wav)
    try:
        model = Transcriber("base.en").model
    except Exception as e:
        pytest.skip(f"Whisper base.en model unavailable: {type(e).__name__}")
    up = ChunkedUploads(tmp_path / "lec")
    uid = up.start(course="PHYS 141")
    try:
        encode(str(wav), tmp_path / "rec.webm", "webm", "libopus")
    except Exception as e:
        pytest.skip(f"libopus unavailable: {type(e).__name__}")
    data = (tmp_path / "rec.webm").read_bytes()
    for offset in range(0, len(data), 4096):
        up.append(uid, offset, data[offset:offset + 4096])
    up.finish(uid, total=len(data))
    assignments, _ = assignments_mock()
    p = LecturePipeline(up, fake_client, LectureNotesDB(assignments),
                        whisper_factory=lambda name: model)
    from assistant.lecture import LectureJob
    p._save(LectureJob(id=uid))
    job = p.job(uid)
    d = up.directory(uid)
    p._transcribe(job, d, d / "transcript.txt")
    text = (d / "transcript.txt").read_text()
    assert rough_match(phrase, text), text
    assert job.duration_seconds and job.progress == 1.0


# ---------- retries never file a second page ----------
def test_retry_after_a_failed_append_resumes_the_same_page(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block(
        "save_lecture_notes", {**NOTES, "key_concepts": [f"c{i}" for i in range(250)]}),
        stop_reason="tool_use")]
    p, up, uid, notion, _, _ = pipeline(tmp_path, fake_client)
    notion.data_sources.retrieve.return_value = {"properties": {"Lecture ID": {}}}
    notion.blocks.children.append.side_effect = [None, RuntimeError("Notion 502")]
    p.submit(uid)
    p.wait(10)
    assert p.job(uid).status == "failed"
    assert notion.pages.create.call_count == 1
    props = notion.pages.create.call_args.kwargs["properties"]
    assert props["Lecture ID"]["rich_text"][0]["text"]["content"] == uid
    notion.blocks.children.append.side_effect = None
    p.submit(uid)
    p.wait(10)
    assert p.job(uid).status == "done"
    assert notion.pages.create.call_count == 1          # no second page
    assert notion.blocks.children.append.call_count == 3  # only the missing batch


def test_page_created_before_a_crash_is_found_by_its_key(tmp_path, fake_client):
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    p, up, uid, notion, _, _ = pipeline(tmp_path, fake_client)
    notion.data_sources.retrieve.return_value = {"properties": {"Lecture ID": {}}}
    notion.data_sources.query.return_value = {"results": [
        {"id": "page-early", "url": "https://notion.so/early"}]}
    p.submit(uid)
    p.wait(10)
    job = p.job(uid)
    assert job.status == "done" and job.notion_url == "https://notion.so/early"
    assert not notion.pages.create.called
    flt = notion.data_sources.query.call_args.kwargs["filter"]
    assert flt == {"property": "Lecture ID", "rich_text": {"equals": uid}}


def test_old_database_gets_the_key_property_added(tmp_path):
    assignments, client = assignments_mock()
    client.data_sources.retrieve.side_effect = lambda data_source_id: (
        {"properties": {}} if data_source_id == "ds-notes" else
        {"properties": {"Course": {"select": {"options": []}}}})
    client.data_sources.query.return_value = {"results": []}
    db = LectureNotesDB(assignments)
    db.create_page(NOTES, "", "2026-10-16", None, key="k" * 24)
    client.data_sources.update.assert_called_once_with(
        data_source_id="ds-notes", properties={"Lecture ID": {"rich_text": {}}})
    assert "Lecture ID" in client.pages.create.call_args.kwargs["properties"]
