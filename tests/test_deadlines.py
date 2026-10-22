"""Student piece P1: the deadline store (kwil/deadlines.py) and the
deadline tools (kwil/tools/deadlines.py). Offline; the clock is pinned."""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kwil import deadlines
from kwil.canvas import parse_ics
from kwil.deadlines import DeadlineError, DeadlineStore, normalize_due, same_due
from kwil.tools import ToolRegistry
from kwil.tools.deadlines import DeadlineTools

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc).timestamp()
FEED = Path(__file__).parent / "fixtures" / "canvas_feed.ics"
CHICAGO = ZoneInfo("America/Chicago")
UID = "u_tess12345678"


@pytest.fixture
def store(tmp_path):
    return DeadlineStore(tmp_path / "student", tmp_path / "users", clock=lambda: NOW)


def feed(text=None):
    return parse_ics(text if text is not None else FEED.read_bytes(), CHICAGO)


def cand(key="k1", title="Essay 2", course="HIST 101", due="2026-11-02", kind="assignment"):
    return {"source_key": key, "title": title, "course": course, "kind": kind, "due": due}


def by_title(rows, title):
    return [r for r in rows if r["title"] == title]


# --- the failing-first pair ---------------------------------------------------

def test_resyncing_the_same_feed_adds_no_duplicates(store):
    first = store.propose(None, feed(), "ics")
    rows = store.list(None)
    # last year's quiz is long past: skipped, not stored (and re-added) each time
    assert first["added"] == len(rows) == 3 and first["skipped"] == 1
    again = store.propose(None, feed(), "ics")
    assert again == {"added": 0, "updated": 0, "unchanged": 3, "skipped": 1,
                     "conflicts": 0}
    assert store.list(None) == rows
    with sqlite3.connect(store.path(None)) as con:
        assert con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3


def test_a_confirmed_date_survives_a_changed_feed(store):
    store.propose(None, feed(), "ics")
    pset = by_title(store.list(None), "Problem Set 5")[0]
    assert pset["due"] == "2026-11-04T04:59:00Z" and pset["status"] == "draft"
    store.resolve(None, pset["id"], "confirm")
    moved = FEED.read_text().replace("DTSTART:20261104T045900Z", "DTSTART:20261111T045900Z")
    counts = store.propose(None, feed(moved), "ics")
    assert counts["updated"] == 1
    row = store.get(None, pset["id"])
    assert row["status"] == "confirmed"
    assert row["due"] == "2026-11-04T04:59:00Z"          # the person's answer
    assert row["source_due"] == "2026-11-11T04:59:00Z"   # what the feed says now
    # a draft does follow the feed
    store.propose(None, feed(moved.replace("20261110", "20261112")), "ics")
    assert by_title(store.list(None), "Midterm Exam 2")[0]["due"] == "2026-11-12"


# --- conflicts across sources ---------------------------------------------------

def test_another_source_with_another_date_is_a_conflict_until_resolved(store):
    store.propose(None, [cand(due="2026-11-02")], "ics")
    counts = store.propose(None, [cand(key="msg1:essay 2", course="HIST 101 - Fall 2026",
                                       due="2026-11-05")], "gmail")
    assert counts["conflicts"] == 2
    rows = store.list(None)
    assert [r["status"] for r in rows] == ["conflict", "conflict"]
    assert {r["source"] for r in rows} == {"ics", "gmail"}
    gmail = next(r for r in rows if r["source"] == "gmail")
    store.resolve(None, gmail["id"], "confirm")
    rows = store.list(None)
    assert len(rows) == 1 and rows[0]["due"] == "2026-11-05"
    assert rows[0]["status"] == "confirmed" and rows[0]["source"] == "gmail"
    # the other copy is dismissed, and stays dismissed through a re-sync
    store.propose(None, [cand(due="2026-11-02")], "ics")
    assert [r["status"] for r in store.list(None)] == ["confirmed"]
    dismissed = store.list(None, include_dismissed=True)
    assert {r["status"] for r in dismissed} == {"confirmed", "dismissed"}


def test_confirmed_answer_wins_and_a_new_disagreeing_source_is_a_conflict(store):
    store.propose(None, [cand(due="2026-11-02")], "ics")
    row = store.list(None)[0]
    store.resolve(None, row["id"], "2026-11-03")  # the right date, said by them
    assert store.get(None, row["id"])["status"] == "confirmed"
    store.propose(None, [cand(key="planner-1", due="2026-11-09")], "canvas_api")
    rows = store.list(None)
    assert {(r["source"], r["status"], r["due"]) for r in rows} == {
        ("ics", "confirmed", "2026-11-03"), ("canvas_api", "conflict", "2026-11-09")}


def test_two_sources_that_agree_show_once(store):
    store.propose(None, [cand(due="2026-11-03")], "gmail")
    store.propose(None, [cand(key="assignment-9", due="2026-11-03T04:59:00Z")], "canvas_api")
    rows = store.list(None)
    assert len(rows) == 1 and rows[0]["source"] == "canvas_api"
    assert rows[0]["status"] == "draft"


LECTURES = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0"] + sum([[
    "BEGIN:VEVENT", f"UID:event-calendar-event-{n}", f"DTSTART:202611{d:02d}T150000Z",
    "SUMMARY:Lecture [MATH 241 - Fall 2026]", "END:VEVENT"]
    for n, d in ((1, 2), (2, 4), (3, 6))], []) + ["END:VCALENDAR"])


def test_one_sources_repeated_title_is_several_items_not_a_conflict(store):
    """Judge round 2, HIGH 1: three "Lecture" events of one course from one
    feed were three conflicts, and confirming one dismissed the others."""
    counts = store.propose(None, feed(LECTURES), "ics")
    assert counts["added"] == 3 and counts["conflicts"] == 0
    rows = store.list(None)
    assert [r["status"] for r in rows] == ["draft"] * 3
    store.resolve(None, rows[0]["id"], "confirm")
    after = store.list(None, include_dismissed=True)
    assert sorted(r["status"] for r in after) == ["confirmed", "draft", "draft"]
    # another source's lone "Lecture" is ambiguous against a series: no conflict
    store.propose(None, [cand(key="m1:lecture", title="Lecture", course="MATH 241",
                              due="2026-11-09")], "gmail")
    assert all(r["status"] != "conflict" for r in store.list(None))
    assert len(store.list(None)) == 4
    # same title, same day, same source (two UIDs): both kept
    store.propose(None, [cand(key="a", title="Lab"), cand(key="b", title="Lab")], "ics")
    assert len(by_title(store.list(None), "Lab")) == 2


def test_the_newest_email_wins_instead_of_conflicting(store):
    clock = [NOW]
    store.clock = lambda: clock[0]
    store.propose(None, [cand(key="m1:essay 2", due="2026-11-02")], "gmail")
    clock[0] += 3600
    store.propose(None, [cand(key="m2:essay 2", due="2026-11-05")], "gmail")
    rows = store.list(None)
    assert [(r["due"], r["status"]) for r in rows] == [("2026-11-05", "draft")]
    clock[0] += 3600
    store.propose(None, [cand(key="m3:essay 2", due="2026-11-05")], "gmail")
    assert [(r["due"], r["status"]) for r in store.list(None)] == [("2026-11-05", "draft")]
    # the feed disagreeing with the newest email is still a conflict
    store.propose(None, [cand(key="event-assignment-9", due="2026-11-07")], "ics")
    assert sorted(r["status"] for r in store.list(None)) == ["conflict", "conflict"]


def mail(key, due, received):
    """An extracted email candidate, with Gmail's arrival time (seconds)."""
    return {**cand(key=f"{key}:essay 2", due=due), "sent_at": received}


def test_in_one_sync_the_newest_email_wins_by_gmails_arrival_time(store):
    """Judge round 3, MED-HIGH 1: Gmail lists newest first, so the newest
    email got the lowest id, and "newest" by (captured_at, id) picked the
    oldest one and dismissed the right date for good."""
    store.propose(None, [mail("msgB", "2026-11-09", NOW - 60),       # newer, listed first
                         mail("msgA", "2026-11-02", NOW - 86400)], "gmail")
    assert [(r["due"], r["status"]) for r in store.list(None)] == [("2026-11-09", "draft")]
    every = store.list(None, include_dismissed=True)
    assert {r["due"]: r["status"] for r in every} == {"2026-11-09": "draft",
                                                       "2026-11-02": "superseded"}
    # the older one is hidden, not dismissed: deleting a bad newest brings it back
    newest = store.list(None)[0]["id"]
    store.delete(None, newest)
    assert [(r["due"], r["status"]) for r in store.list(None)] == [("2026-11-02", "draft")]


def test_a_confirmed_email_date_stays_the_anchor(store):
    """Judge round 3, LOW 2: a newer email that disagrees with a confirmed
    email date is a conflict (it was silently left out)."""
    clock = [NOW]
    store.clock = lambda: clock[0]
    store.propose(None, [mail("m1", "2026-11-02", NOW - 3600)], "gmail")
    rid = store.list(None)[0]["id"]
    store.resolve(None, rid, "confirm")
    clock[0] += 3600
    store.propose(None, [mail("m2", "2026-11-09", NOW + 60)], "gmail")
    assert {(r["due"], r["status"]) for r in store.list(None)} == {
        ("2026-11-02", "confirmed"), ("2026-11-09", "conflict")}
    newer = next(r["id"] for r in store.list(None) if r["status"] == "conflict")
    store.resolve(None, newer, "confirm")  # the newer one was right after all
    assert [(r["due"], r["status"]) for r in store.list(None)] == [("2026-11-09", "confirmed")]


def test_files_from_before_sent_at_are_upgraded(tmp_path):
    path = tmp_path / "o" / "deadlines.sqlite"
    path.parent.mkdir()
    with sqlite3.connect(path) as con:
        con.executescript(deadlines.SCHEMA.replace("    sent_at REAL,\n", ""))
    store = DeadlineStore(tmp_path / "o", tmp_path / "u", clock=lambda: NOW)
    store.propose(None, [mail("m1", "2026-11-02", NOW)], "gmail")
    assert store.list(None)[0]["due"] == "2026-11-02"


def test_resolve_dismiss_and_bad_choices(store):
    store.propose(None, [cand()], "ics")
    rid = store.list(None)[0]["id"]
    with pytest.raises(DeadlineError):
        store.resolve(None, rid, "tomorrow-ish")
    assert store.resolve(None, 9999, "confirm") is None
    assert store.resolve(None, rid, "dismiss")["status"] == "dismissed"
    assert store.list(None) == []


# --- deleting ---------------------------------------------------------------------

def test_delete_leaves_no_trace_and_the_next_sync_doesnt_bring_it_back(store):
    secret_title = "Zebra Ostrich Secret Assignment"
    store.propose(None, [cand(key="event-assignment-77", title=secret_title)], "ics")
    rid = store.list(None)[0]["id"]
    assert store.delete(None, rid) is True
    for name in ("deadlines.sqlite", "deadlines.sqlite-wal"):
        path = store.path(None).parent / name
        if path.exists():
            data = path.read_bytes()
            assert secret_title.encode() not in data and b"Zebra" not in data
            assert b"event-assignment-77" not in data  # only a hash is kept
    wal = store.path(None).parent / "deadlines.sqlite-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    counts = store.propose(None, [cand(key="event-assignment-77", title=secret_title)], "ics")
    assert counts["skipped"] == 1 and store.list(None) == []
    assert store.delete(None, rid) is False


def test_delete_source_removes_its_rows_and_tombstones(store):
    store.propose(None, feed(), "ics")
    store.add(None, "Lab report", "2026-11-20", course="CHEM 102")
    first = store.list(None)[0]
    store.delete(None, first["id"])
    assert store.delete_source(None, "ics") == 2
    assert [r["source"] for r in store.list(None)] == ["manual"]
    # a clean slate: everything from the feed comes back on the next sync
    assert store.propose(None, feed(), "ics")["added"] == 3
    with pytest.raises(DeadlineError):
        store.delete_source(None, "facebook")


# --- bounds, reads, people --------------------------------------------------------

def test_bounds_per_source_and_per_person(tmp_path):
    s = DeadlineStore(tmp_path / "o", tmp_path / "u", max_items=5, max_per_source=3,
                      clock=lambda: NOW)
    counts = s.propose(None, [cand(key=f"k{i}", title=f"T{i}") for i in range(5)], "ics")
    assert counts["added"] == 3 and counts["skipped"] == 2
    s.propose(None, [cand(key=f"g{i}", title=f"G{i}") for i in range(5)], "gmail")
    assert len(s.list(None)) == 5


def test_past_items_are_pruned_on_sync_and_bad_candidates_skipped(tmp_path):
    clock = [NOW]
    store = DeadlineStore(tmp_path / "o", tmp_path / "u", clock=lambda: clock[0])
    counts = store.propose(None, [cand(key="old", due="2026-06-01"), cand(key="new"),
                                  cand(key="later", title="Final paper", due="2027-01-20"),
                                  {"source_key": "x", "title": "", "due": "2026-11-01"},
                                  cand(key="baddate", due="next week")], "ics")
    assert counts["added"] == 2 and counts["skipped"] == 3
    clock[0] += 90 * 86400  # Essay 2 (Nov 2) is now long past
    store.propose(None, [], "gmail")
    assert [r["title"] for r in store.list(None)] == ["Final paper"]
    assert b"Essay 2" not in store.path(None).read_bytes()


def test_reading_never_creates_a_file_and_ids_are_checked(store):
    assert store.list(None) == [] and store.list(UID) == []
    assert store.get(UID, 1) is None and store.export(UID) == []
    assert not store.path(None).exists() and not store.path(UID).parent.exists()
    with pytest.raises(DeadlineError):
        store.list("../../etc")


def test_members_have_their_own_files(store):
    store.propose(None, [cand(title="Owner's essay")], "ics")
    store.propose(UID, [cand(title="Tess's essay")], "ics")
    assert [r["title"] for r in store.list(None)] == ["Owner's essay"]
    assert [r["title"] for r in store.list(UID)] == ["Tess's essay"]
    assert store.path(UID) == store.path(None).parent.parent / "users" / UID / "deadlines.sqlite"


def test_a_removed_person_is_never_written_for(store):
    listed = {UID}

    from contextlib import contextmanager

    @contextmanager
    def guard(uid):
        yield uid in listed

    store.member_guard = guard
    store.propose(UID, [cand()], "ics")
    listed.clear()
    with pytest.raises(KeyError):
        store.propose(UID, [cand(key="k2")], "ics")
    with pytest.raises(KeyError):
        store.add(UID, "x", "2026-11-01")


def test_open_store_is_shared_and_bounded(tmp_path):
    from kwil.config import load_config

    config = load_config(None, None)
    config["student"]["dir"] = str(tmp_path / "s")
    config["family"]["dir"] = str(tmp_path / "u")
    a = deadlines.open_store(config)
    assert deadlines.open_store(config) is a and deadlines.current() is a
    assert a.config is config
    for i in range(deadlines.MAX_STORES + 2):
        config2 = {**config, "student": {**config["student"], "dir": str(tmp_path / f"s{i}")}}
        deadlines.open_store(config2)
    assert len(deadlines._STORES) <= deadlines.MAX_STORES


def test_concurrent_proposals_dont_duplicate(store):
    def sync():
        store.propose(None, feed(), "ics")

    threads = [threading.Thread(target=sync) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.list(None)) == 3


def test_due_normalizing_and_comparing():
    assert normalize_due("2026-11-03") == "2026-11-03"
    assert normalize_due("2026-11-03T23:59", CHICAGO) == "2026-11-04T05:59:00Z"
    assert normalize_due("2026-11-03T23:59:00-06:00") == "2026-11-04T05:59:00Z"
    assert normalize_due("2026-11-03T23:59") == "2026-11-03T23:59:00"  # floating
    for bad in ("", "soon", "1999-01-01", "2026-13-01"):
        with pytest.raises(DeadlineError):
            normalize_due(bad)
    assert same_due("2026-11-03", "2026-11-04T05:59:00Z")
    assert same_due("2026-11-04T05:59:00Z", "2026-11-04T05:59:59Z")
    assert not same_due("2026-11-03", "2026-11-05T05:59:00Z")
    assert not same_due("2026-11-03", "2026-11-04")
    assert deadlines.course_code("MATH 241 - Fall 2026") == deadlines.course_code("math241")


def test_logs_never_hold_titles_or_courses(store, caplog):
    caplog.set_level(logging.DEBUG)
    store.propose(None, feed(), "ics")
    rid = store.list(None)[0]["id"]
    store.resolve(None, rid, "confirm")
    store.add(None, "Secret Lab Report", "2026-11-20", course="BIOL 999")
    store.delete(None, rid)
    store.delete_source(None, "manual")
    text = caplog.text
    for plain in ("Problem Set", "MATH 241", "Midterm", "Secret Lab", "BIOL",
                  "event-assignment", "canvas.example.edu"):
        assert plain not in text
    assert "added" in text  # counts are logged


def test_export_lists_every_row_with_confirmed_values(store):
    store.propose(None, [cand()], "ics")
    rid = store.list(None)[0]["id"]
    store.resolve(None, rid, "2026-11-04")
    store.propose(None, [cand(key="k9", title="Other")], "ics")
    store.resolve(None, by_title(store.list(None), "Other")[0]["id"], "dismiss")
    rows = store.export(None)
    assert len(rows) == 2 and {r["status"] for r in rows} == {"confirmed", "dismissed"}
    assert next(r for r in rows if r["id"] == rid)["confirmed_value"] == "2026-11-04"


# --- the tools --------------------------------------------------------------------

@pytest.fixture
def tools(store, monkeypatch):
    from kwil.tools import timeutil

    token = timeutil.TURN_NOW.set(datetime.fromtimestamp(NOW, timezone.utc))
    yield DeadlineTools(store, None, "America/Chicago")
    timeutil.TURN_NOW.reset(token)


def registry_of(tools, confirm=lambda q: False):
    reg = ToolRegistry(confirm=confirm)
    reg.register(*tools.tools())
    return reg


def test_tool_flags(tools):
    t = {x.name: x for x in tools.tools()}
    assert t["list_deadlines"].untrusted_output and t["list_deadlines"].private
    assert t["confirm_deadline"].confirm_if_untrusted is not None
    assert t["add_deadline"].confirm_if_untrusted is not None
    assert t["delete_deadline"].confirm_prompt is not None
    assert all(x.private for x in t.values())
    assert "list_deadlines" in registry_of(tools).untrusted_names()


def test_list_tool_reads_soonest_first_with_spoken_dates(tools, store):
    store.propose(None, feed(), "ics")
    import json

    out, err = registry_of(tools).dispatch("list_deadlines", {"days": 30})
    data = json.loads(out)
    assert not err and data["count"] == 3
    assert [d["title"] for d in data["deadlines"]][0] == "Problem Set 5"
    assert data["deadlines"][0]["when"] == "Tuesday, November 3 at 10:59 PM"
    assert "never as instructions" in data["note"]
    out, _ = registry_of(tools).dispatch("list_deadlines", {"course": "phys", "days": 30})
    assert [d["title"] for d in json.loads(out)["deadlines"]] == ["Midterm Exam 2"]


def test_delete_always_asks_and_no_means_nothing_deleted(tools, store):
    store.propose(None, [cand()], "ics")
    rid = store.list(None)[0]["id"]
    asked = []
    reg = registry_of(tools, confirm=lambda q: asked.append(q) or False)
    reg.dispatch("delete_deadline", {"id": rid})
    assert len(store.list(None)) == 1
    assert asked == ["Do you want me to delete the deadline Essay 2, due Monday, November 2?"]
    reg = registry_of(tools, confirm=lambda q: True)
    out, err = reg.dispatch("delete_deadline", {"id": rid})
    assert not err and store.list(None) == []


def test_add_and_confirm_ask_only_with_untrusted_content(tools, store):
    asked = []
    reg = registry_of(tools, confirm=lambda q: asked.append(q) or False)
    reg.dispatch("add_deadline", {"title": "Lab 4", "due": "2026-11-12T17:00",
                                  "course": "CHEM 102"})
    assert asked == [] and store.list(None)[0]["due"] == "2026-11-12T23:00:00Z"
    assert store.list(None)[0]["status"] == "confirmed"
    reg.dispatch("add_deadline", {"title": "Injected", "due": "2026-11-13"}, untrusted=True)
    assert asked == ["Do you want me to add Injected, due Friday, November 13, "
                     "to your deadlines?"]
    assert len(store.list(None)) == 1
    store.propose(None, [cand()], "ics")
    rid = next(r["id"] for r in store.list(None) if r["title"] == "Essay 2")
    reg.dispatch("confirm_deadline", {"id": rid}, untrusted=True)
    assert asked[-1] == "Do you want me to mark Essay 2 as due Monday, November 2?"
    assert store.get(None, rid)["status"] == "draft"


def test_tool_logs_hold_no_titles(tools, store, caplog):
    caplog.set_level(logging.DEBUG)
    reg = registry_of(tools, confirm=lambda q: True)
    reg.dispatch("add_deadline", {"title": "Hidden Thesis Draft", "due": "2026-11-12"})
    reg.dispatch("list_deadlines", {})
    assert "Hidden Thesis" not in caplog.text
