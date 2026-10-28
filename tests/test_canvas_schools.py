"""Which way into Canvas suits a school (kwil/canvas_schools.py): the
sourced seed's schema (every shipped entry, plus a fixture seed: the
shipped one is empty until someone has opened each school's page), host
and domain matching, what this server learned (a newer result beats the
seed, an older one never does), the bound on learned hosts, no person's id
in the file, the private atomic write, and no host in the logs."""

import json
import logging
import os
import sys
from datetime import date

import pytest

from kwil import canvas_schools
from kwil.canvas_schools import Policies, check_entry, load_seed, normalize

FIXTURE = [
    {"name": "Example State University", "hosts": ["canvas.example.edu"],
     "domains": ["exampleu.edu"], "student_tokens": "blocked", "since": "2025-11-14",
     "source": "https://it.exampleu.edu/canvas-tokens", "checked": "2026-10-01",
     "note": "students request a token from IT"},
    {"name": "Hosted College", "hosts": ["hosted.instructure.com"], "domains": [],
     "student_tokens": "allowed", "since": None,
     "source": "https://help.hosted.edu/tokens", "checked": "2026-09-01", "note": ""},
]


def seed_file(tmp_path, schools=FIXTURE):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"version": 1, "schools": schools}), encoding="utf-8")
    return path


class Today:
    def __init__(self, day="2026-10-28"):
        self.day = date.fromisoformat(day)

    def __call__(self):
        return self.day


@pytest.fixture
def p(tmp_path):
    today = Today()
    policies = Policies(tmp_path / "student", seed=seed_file(tmp_path), today=today)
    policies.clock = today
    return policies


# --- the seed ---------------------------------------------------------------------------

def test_the_shipped_seed_is_valid_and_every_entry_is_sourced():
    """Every entry (none yet: added once its source page was opened) has
    an https source, an ISO checked date and blocked/allowed, and the seed
    loads without dropping any."""
    raw = json.loads(canvas_schools.SEED.read_text(encoding="utf-8"))
    assert raw["version"] == 1 and isinstance(raw["schools"], list)
    for entry in raw["schools"]:
        checked = check_entry(entry)
        assert checked["source"].startswith("https://")
        assert checked["student_tokens"] in ("blocked", "allowed")
        assert isinstance(checked["checked"], date)
        assert set(entry) <= canvas_schools.ENTRY_KEYS
    assert len(load_seed()) == len(raw["schools"])


def test_the_fixture_seed_passes_the_schema(tmp_path):
    loaded = load_seed(seed_file(tmp_path))
    assert [e["name"] for e in loaded] == [e["name"] for e in FIXTURE]
    assert loaded[0]["checked"] == date(2026, 10, 1)


@pytest.mark.parametrize("change, why", [
    ({"source": "http://it.exampleu.edu/x"}, "source"),
    ({"source": "https://it.exampleu.edu/a b"}, "source"),
    ({"checked": "2026-13-01"}, "checked"),
    ({"checked": "Oct 1 2026"}, "checked"),
    ({"since": "2025/11/14"}, "since"),
    ({"student_tokens": "maybe"}, "student_tokens"),
    ({"hosts": ["Canvas.Example.edu"]}, "hosts"),
    ({"hosts": ["10.0.0.1"]}, "hosts"),
    ({"hosts": ["canvas.school.local"]}, "hosts"),
    ({"domains": ["instructure.com"]}, "instructure"),
    ({"domains": ["eu.instructure.com"]}, "instructure"),
    ({"hosts": [], "domains": []}, "at least one"),
    ({"name": ""}, "name"),
    ({"grades": True}, "unknown field"),
])
def test_the_schema_refuses_unsourced_or_malformed_entries(change, why):
    with pytest.raises(ValueError, match=why):
        check_entry({**FIXTURE[0], **change})


def test_a_bad_entry_is_skipped_without_its_content_in_the_log(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    bad = {**FIXTURE[0], "source": "http://secret-school.edu/x"}
    loaded = load_seed(seed_file(tmp_path, [bad, FIXTURE[1]]))
    assert [e["name"] for e in loaded] == ["Hosted College"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "entry 0" in text and "secret-school" not in text
    # an unreadable or wrong-version seed is an empty one
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert load_seed(tmp_path / "bad.json") == []
    (tmp_path / "v2.json").write_text('{"version": 2, "schools": []}', encoding="utf-8")
    assert load_seed(tmp_path / "v2.json") == []


# --- matching ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, host", [
    ("canvas.example.edu", "canvas.example.edu"),
    ("  Canvas.Example.EDU. ", "canvas.example.edu"),
    ("https://canvas.example.edu/courses/9", "canvas.example.edu"),
    ("https://canvas.example.edu:443/", "canvas.example.edu"),
    ("http://canvas.example.edu", None),
    ("https://me:pw@canvas.example.edu", None),
    ("https://canvas.example.edu:8443", None),
    ("canvas.example.edu/x", None),
    ("10.0.0.1", None), ("localhost", None), ("canvas.school.local", None),
    ("", None), (None, None), (42, None),
])
def test_normalize(value, host):
    assert normalize(value) == host


def test_hosts_and_domain_suffixes_match(p):
    assert p.policy("canvas.example.edu") == "blocked"           # listed
    assert p.policy("https://canvas.example.edu/x") == "blocked"
    assert p.policy("canvas.exampleu.edu") == "blocked"          # ends in .exampleu.edu
    assert p.policy("learn.canvas.exampleu.edu") == "blocked"
    assert p.policy("exampleu.edu.evil.com") == "unknown"        # a suffix, not a substring
    assert p.policy("notexampleu.edu") == "unknown"
    assert p.policy("hosted.instructure.com") == "allowed"       # instructure: listed only
    assert p.policy("other.instructure.com") == "unknown"
    assert p.policy("not a host") == "unknown"
    assert p.policy(None) == "unknown"


# --- learning ---------------------------------------------------------------------------

def test_learned_beats_the_seed_only_when_newer(p):
    """The seed was checked 2026-10-01: a result learned that day or
    before never overrides it; one learned after does (policies change)."""
    p.clock.day = date(2026, 10, 1)
    assert p.record("canvas.example.edu", "allowed")
    assert p.policy("canvas.example.edu") == "blocked"
    p.clock.day = date(2026, 10, 2)
    assert p.record("canvas.example.edu", "allowed")
    assert p.policy("canvas.example.edu") == "allowed"
    # without a seed entry, what was learned decides at once
    assert p.policy("canvas.newschool.edu") == "unknown"
    p.record("canvas.newschool.edu", "blocked")
    assert p.policy("canvas.newschool.edu") == "blocked"


def test_the_latest_outcome_wins_and_a_same_day_tie_is_unknown(p):
    host = "canvas.newschool.edu"
    p.clock.day = date(2026, 10, 2)
    p.record(host, "allowed")
    p.clock.day = date(2026, 10, 3)
    p.record(host, "blocked")
    assert p.policy(host) == "blocked"          # newer than the allowed one
    # both on the same day: the one counted more often, a tie is no verdict
    other = "canvas.otherschool.edu"
    p.record(other, "blocked")
    p.record(other, "allowed")
    assert p.policy(other) == "unknown"
    p.record(other, "allowed")
    assert p.policy(other) == "allowed"


def test_only_hosts_and_outcomes_are_recorded(p):
    assert not p.record("canvas.newschool.edu", "maybe")
    assert not p.record("https://localhost", "blocked")
    assert not p.record("", "allowed")
    assert not p.path.exists()


def test_the_file_holds_hosts_and_counts_only(p):
    p.record("https://canvas.newschool.edu/courses/1", "allowed")
    p.record("canvas.newschool.edu", "allowed")
    data = json.loads(p.path.read_text(encoding="utf-8"))
    assert data == {"canvas.newschool.edu": {
        "allowed": 2, "blocked": 0, "allowed_at": "2026-10-28", "blocked_at": None,
        "last": "2026-10-28"}}
    if sys.platform != "win32":
        assert os.stat(p.path).st_mode & 0o777 == 0o600


def test_learned_hosts_are_bounded_least_recently_seen_first(p, monkeypatch):
    monkeypatch.setattr(canvas_schools, "MAX_HOSTS", 5)
    for i in range(7):
        p.clock.day = date(2026, 10, 1 + i)
        assert p.record(f"canvas.school{i}.edu", "blocked")
    data = json.loads(p.path.read_text(encoding="utf-8"))
    assert sorted(data) == [f"canvas.school{i}.edu" for i in range(2, 7)]
    assert p.policy("canvas.school0.edu") == "unknown"


def test_the_default_bound_is_a_thousand_hosts():
    assert canvas_schools.MAX_HOSTS == 1000


def test_writes_are_private_and_atomic(p, monkeypatch):
    from kwil.tools import google_auth

    calls = []
    real = google_auth.write_private

    def spy(path, text):
        calls.append(path)
        real(path, text)

    monkeypatch.setattr(google_auth, "write_private", spy)
    p.record("canvas.newschool.edu", "blocked")
    assert calls == [p.path]
    before = p.path.read_text(encoding="utf-8")

    def broken(path, text):
        raise PermissionError("locked")

    monkeypatch.setattr(google_auth, "write_private", broken)
    assert not p.record("canvas.other.edu", "blocked")
    assert p.path.read_text(encoding="utf-8") == before   # the old file stands
    assert [x.name for x in p.path.parent.iterdir()] == [canvas_schools.POLICY_FILE]


def test_a_tampered_file_is_read_safely(p):
    p.path.parent.mkdir(parents=True)
    p.path.write_text(json.dumps({
        "canvas.newschool.edu": {"allowed": 3, "blocked": "x", "allowed_at": "2026-10-05",
                                 "blocked_at": "nope", "last": "2026-10-05"},
        "NOT A HOST": {"allowed": 1, "last": "2026-10-05"},
        "canvas.nolast.edu": {"allowed": 1},
        "canvas.list.edu": [1, 2]}), encoding="utf-8")
    assert p.policy("canvas.newschool.edu") == "allowed"
    assert p.policy("canvas.nolast.edu") == "unknown"
    p.path.write_text("[", encoding="utf-8")
    assert p.policy("canvas.newschool.edu") == "unknown"


def test_no_host_is_ever_logged(p, caplog):
    caplog.set_level(logging.DEBUG)
    p.record("canvas.secretschool.edu", "blocked")
    p.record("canvas.secretschool.edu", "allowed")
    p.policy("canvas.secretschool.edu")
    ours = [r for r in caplog.records if r.name.startswith("kwil")]
    assert any(r.levelno == logging.INFO for r in ours)    # counts are logged
    for r in ours:
        assert "secretschool" not in r.getMessage()
