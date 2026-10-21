"""Family accounts on the cloud server (assistant/family.py): the owner
invites someone, they accept a one-time link and get their own history and
language, and never anything of Kyle's (Notion, memory, notes, history,
dev team, lecture/tutor, Google, other people's audio).

All offline: the mocked Anthropic client, real SQLite history in tmp_path."""

import json
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import family as family_mod, webapp, webfeatures  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import DEFAULTS, load_config, redact  # noqa: E402
from assistant.history import HistoryStore, Recorder  # noqa: E402
from assistant.tools import Tool, ToolRegistry  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import (make_response, request_text, sent_text,  # noqa: E402
                            text_block)
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

DAY = 86400
# what the owner's registry has and a family member must never get
OWNER_TOOLS = ("list_assignments", "update_assignment_status", "search_memory",
               "remember_note", "recall_notes", "search_conversations",
               "resume_conversation", "message_dev_team", "list_events",
               "ha_list", "ha_state", "ha_control", "chat_channels", "chat_read",
               "chat_search", "chat_files", "chat_read_file", "chat_save_digest")


def owner_registry():
    reg = ToolRegistry()
    for name in OWNER_TOOLS:
        reg.register(Tool(name=name, description=name,
                          input_schema={"type": "object", "properties": {}},
                          handler=lambda **kw: {"ok": True}))
    return reg


class Env(SimpleNamespace):
    def client(self):
        return TestClient(self.app, base_url=BASE)

    def invite(self, owner, name="Mom", language="zh", **extra):
        r = owner.post("/api/invites", json={"name": name, "language": language,
                                             **extra})
        assert r.status_code == 200, r.text
        return r.json()

    def member(self, owner, **kw):
        """A signed-in family member's client (and the invite it used)."""
        link = self.invite(owner, **kw)
        c = self.client()
        r = c.post("/api/invite/accept", json={"invite": token_of(link["url"])})
        assert r.status_code == 200, r.text
        return c, link


def delete(client, url):
    """DELETE as the page sends it (a body: unsafe requests need a length)."""
    return client.request("DELETE", url, content=b"{}",
                          headers={"Content-Type": "application/json"})


def token_of(url: str) -> str:
    return url.split("#invite=", 1)[1]


@pytest.fixture
def env(fake_client, tmp_path):
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["history"]["path"] = str(tmp_path / "owner.sqlite")
    clock = SimpleNamespace(now=1_700_000_000.0)
    fam = family_mod.Family(config, client=fake_client, clock=lambda: clock.now)
    owner_store = HistoryStore(tmp_path / "owner.sqlite", eventlog=False)
    memory = MagicMock()
    memory.retrieve.return_value = ["Kyle's secret project note"]
    memory.search_conversations.return_value = []
    memory.vectors_for.return_value = []

    def chat_factory(registry):
        rec = Recorder(owner_store, "web", run_async=False, client_fn=lambda: fake_client)
        return ClaudeChat(client=fake_client, model="test-model", tools=registry,
                          recorder=rec, memory=memory)

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(),
                                    tools=owner_registry(), clock=Clock(),
                                    history=owner_store, memory=memory, family=fam)
    limiter = webapp.LoginLimiter(per_ip=3, global_delay=0)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, limiter=limiter,
                            sessions_file=tmp_path / "sessions.json")
    feats = webfeatures.Features(config, assistant, client=fake_client,
                                 assignments=MagicMock(), pipeline=MagicMock())
    webfeatures.install(app, feats)
    e = Env(app=app, assistant=assistant, family=fam, clock=clock, config=config,
            owner_store=owner_store, memory=memory, fake_client=fake_client,
            tmp=tmp_path, limiter=limiter)
    yield e
    fam.close()
    owner_store.close()


@pytest.fixture
def owner(env):
    c = env.client()
    login(c)
    return c


def seed_owner_conversation(store) -> str:
    cid = store.create("wake")
    store.append(cid, "user", "Kyle's private question")
    store.append(cid, "assistant", "Kyle's private answer")
    store.finish(cid, "Kyle's thing", "About Kyle's thing.")
    return cid


# --- inviting -------------------------------------------------------------------

def test_inviting_needs_the_owner(env, owner):
    anon = env.client()
    assert anon.post("/api/invites", json={"name": "Mom"}).status_code == 401
    assert anon.get("/api/users").status_code == 401
    link = env.invite(owner)
    assert link["url"].startswith("https://testserver/#invite=hcinv.")
    assert link["expires_at"] == int(env.clock.now) + 30 * DAY  # [family] invite_days
    assert link["user"]["status"] == "invited" and "invite_hash" not in link["user"]
    mom, _ = env.member(owner, name="Dad", language="en")
    for method, path in [("POST", "/api/invites"), ("GET", "/api/users"),
                         ("DELETE", f"/api/users/{link['user']['id']}"),
                         ("POST", f"/api/users/{link['user']['id']}/invite")]:
        r = mom.request(method, path, json={"name": "Eve"})
        assert r.status_code == 404, (method, path)
    assert len(owner.get("/api/users").json()["users"]) == 2


def test_remove_by_post_is_owner_only(env, owner):
    """POST /api/users/{id}/remove: what the page uses (a cross-origin
    DELETE fails the failover preflight)."""
    link = env.invite(owner)
    uid = link["user"]["id"]
    mom, _ = env.member(owner, name="Dad", language="en")
    assert env.client().post(f"/api/users/{uid}/remove", json={}).status_code == 401
    assert mom.post(f"/api/users/{uid}/remove", json={}).status_code == 404
    assert owner.post(f"/api/users/{uid}/remove", json={}).status_code == 200
    assert uid not in [u["id"] for u in owner.get("/api/users").json()["users"]]
    assert owner.post(f"/api/users/{uid}/remove", json={}).status_code == 404


def test_users_file_holds_only_the_hash(env, owner):
    link = env.invite(owner)
    token = token_of(link["url"])
    text = (env.tmp / "users" / "users.json").read_text(encoding="utf-8")
    assert token not in text and token[6:] not in text
    assert family_mod.hash_token(token) in text
    if sys.platform != "win32":
        assert (env.tmp / "users" / "users.json").stat().st_mode & 0o077 == 0
    leftovers = [p.name for p in (env.tmp / "users").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_invite_checks_and_limits(env, owner):
    assert owner.post("/api/invites", json={"name": ""}).status_code == 400
    assert owner.post("/api/invites", json={"name": "x" * 41}).status_code == 400
    assert owner.post("/api/invites", json={"name": "A", "language": "fr"}).status_code == 400
    assert owner.post("/api/invites", json={"name": "A", "timezone": "Mars/Base"}
                      ).status_code == 400
    env.family.max_users = 2
    env.invite(owner, name="A")
    env.invite(owner, name="B", timezone="Asia/Shanghai")
    assert owner.post("/api/invites", json={"name": "C"}).status_code == 409


# --- accepting ------------------------------------------------------------------

def test_an_invite_works_once_and_wrong_ones_count_toward_the_lockout(env, owner):
    link = env.invite(owner)
    token = token_of(link["url"])
    phone = env.client()
    r = phone.post("/api/invite/accept", json={"invite": token})
    assert r.status_code == 200 and webapp.COOKIE in r.headers["set-cookie"]
    state = phone.get("/api/state").json()
    assert state["owner"] is False and state["family"] is False
    assert state["language"] == "zh"
    assert env.family.get(link["user"]["id"])["status"] == "active"
    assert env.family.get(link["user"]["id"])["invite_hash"] is None
    # used: refused, and counted like a wrong token
    other = env.client()
    assert other.post("/api/invite/accept", json={"invite": token}).status_code == 401
    # expired
    late = env.invite(owner, name="Late")
    env.clock.now += 31 * DAY
    assert other.post("/api/invite/accept",
                      json={"invite": token_of(late["url"])}).status_code == 401
    assert other.post("/api/invite/accept", json={"invite": "hcinv.nope"}).status_code == 401
    r = other.post("/api/invite/accept", json={"invite": "hcinv.nope"})
    assert r.status_code == 429  # locked out (per_ip=3), as for wrong WEB_TOKENs
    login(other)  # the owner's token always still works
    assert other.get("/api/state").json()["owner"] is True


def test_a_new_link_replaces_the_old_one(env, owner):
    link = env.invite(owner)
    again = owner.post(f"/api/users/{link['user']['id']}/invite").json()
    assert again["url"] != link["url"]
    c = env.client()
    assert c.post("/api/invite/accept",
                  json={"invite": token_of(link["url"])}).status_code == 401
    assert c.post("/api/invite/accept",
                  json={"invite": token_of(again["url"])}).status_code == 200


def test_invite_for_a_removed_person_fails(env, owner):
    link = env.invite(owner)
    assert delete(owner, f"/api/users/{link['user']['id']}").status_code == 200
    c = env.client()
    assert c.post("/api/invite/accept",
                  json={"invite": token_of(link["url"])}).status_code == 401


# --- isolation ------------------------------------------------------------------

def test_a_family_member_has_web_tools_only(env, owner):
    env.config["web"]["location"] = {"city": "Kyle's town", "region": "", "country": "",
                                     "timezone": ""}
    mom, link = env.member(owner, name="Mom", language="en", timezone="Asia/Shanghai",
                          tier="family")  # the owner's key (no key of her own)
    uid = link["user"]["id"]
    ctx = env.family.context(uid)
    assert ctx.registry.tools == {}
    assert {t["name"] for t in ctx.registry.server_tools} == {"web_search", "web_fetch"}
    search = next(t for t in ctx.registry.server_tools if t["name"] == "web_search")
    # their time zone, never the owner's [web] location
    assert search["user_location"] == {"type": "approximate", "timezone": "Asia/Shanghai"}
    env.fake_client.queue.append(make_response(text_block("Hi Mom.")))
    r = mom.post("/api/turn", json={"text": "hello"})
    assert r.status_code == 200
    call = env.fake_client.calls[-1]
    names = {t["name"] for t in call.get("tools", [])}
    assert names <= {"web_search", "web_fetch"}
    assert not names & set(OWNER_TOOLS)
    assert "Kyle" not in sent_text(call) and "Mom" in request_text(call)
    assert "secret project" not in sent_text(call)
    env.memory.retrieve.assert_not_called()  # no Pinecone for family
    session = next(s for s in env.assistant.sessions.values() if s.user == uid)
    assert session.registry.tools == {} and session.chat.memory is None
    assert session.history is ctx.history and session.history is not env.owner_store
    # their own history file, not synced (no event log)
    assert (env.tmp / "users" / uid / "history.sqlite").exists()
    assert session.history.eventlog is None


def test_a_family_member_cannot_see_the_owners_conversations(env, owner):
    cid = seed_owner_conversation(env.owner_store)
    assert owner.get(f"/api/conversations/{cid}").status_code == 200
    mom, _ = env.member(owner, tier="family")  # on the owner's key
    assert mom.get(f"/api/conversations/{cid}").status_code == 404
    assert mom.get(f"/api/conversations/{cid}/export").status_code == 404
    assert mom.post(f"/api/conversations/{cid}/continue").status_code == 404
    assert delete(mom, f"/api/conversations/{cid}").status_code == 404
    assert mom.get("/api/conversations").json()["conversations"] == []
    assert mom.get("/api/conversations?q=Kyle").json()["conversations"] == []
    env.memory.search_conversations.assert_not_called()
    assert env.owner_store.get(cid)["title"] == "Kyle's thing"  # not deleted
    # and the owner doesn't see theirs
    env.fake_client.queue.append(make_response(text_block("你好")))
    assert mom.post("/api/turn", json={"text": "你好"}).status_code == 200
    mine = mom.get("/api/conversations").json()["conversations"]
    assert len(mine) == 1
    assert owner.get(f"/api/conversations/{mine[0]['id']}").status_code == 404


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/lecture/courses"), ("POST", "/api/lecture/start"),
    ("GET", "/api/lectures"), ("POST", "/api/tutor"),
    ("GET", "/api/lecture/abcdefghijklmnopqrstuvwx")])
def test_owner_features_are_not_found_for_family(env, owner, method, path):
    mom, _ = env.member(owner)
    assert mom.request(method, path, json={}).status_code == 404
    if path in ("/api/lecture/courses", "/api/lectures"):  # still the owner's
        assert owner.request(method, path).status_code == 200


def test_family_never_gets_the_owners_google(fake_client, tmp_path):
    """The owner's "connect Google" (config.mom.toml style) is his: a family
    member's status, connect and page are about their own account only
    (test_family_google.py), never his connected token."""
    from fastapi.testclient import TestClient as TC

    from assistant import google_web

    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["webapp"].update(google_connect=True, public_origin="https://pc.example")
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"web": {"client_id": "c", "client_secret": "s",
                                               "auth_uri": "https://a", "token_uri": "https://t"}}))
    kyle_token = tmp_path / "token.json"
    kyle_token.write_text(json.dumps({"token": "ya29.kyle", "refresh_token": "r"}))
    config["calendar"].update(credentials=str(client_file), token=str(kyle_token))
    fam = family_mod.Family(config, client=fake_client)
    assistant = webapp.WebAssistant(
        lambda r: ClaudeChat(client=fake_client, tools=r), FakeTranscriber(),
        PrintTTS(), clock=Clock(), family=fam)
    google = google_web.GoogleConnect(config, assistant.tools, family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, google=google)
    owner = TC(app, base_url=BASE)
    login(owner)
    assert owner.get("/api/google/status").json()["connected"] is True
    assert 'data-google' in owner.get("/").text
    _, token = fam.invite("Mom", "zh", tier="family")
    mom = TC(app, base_url=BASE)
    assert mom.post("/api/invite/accept", json={"invite": token}).status_code == 200
    # no family web client set up: her own Google is off, and Kyle's isn't hers
    assert mom.get("/api/google/status").json() == {
        "enabled": False, "connected": False, "disconnect": True, "gmail": True,
        "note": None}
    assert mom.post("/api/google/connect", json={}).status_code == 503
    page = mom.get("/").text
    assert '<html lang="zh-CN">' in page and "data-google" not in page
    assert not any(t in fam.context(fam.members()[0]["id"]).registry.tools
                   for t in ("list_calendar_events", "email_summary"))
    fam.close()


def test_only_the_owner_sees_the_api_cost(env, owner):
    """usage.py: the monthly estimate is the owner's business; a family
    member's /api/state never has it, even after their own turns."""
    from assistant import usage
    from tests.conftest import make_usage

    mom, _ = env.member(owner, language="en", tier="family")  # on the owner's key
    env.fake_client.queue.append(make_response(text_block("Hi."),
                                               usage=make_usage(1000, 100)))
    assert mom.post("/api/turn", json={"text": "hello"}).status_code == 200
    assert usage.month_usd() > 0
    assert owner.get("/api/state").json()["month_usd"] == usage.month_usd()
    assert "month_usd" not in mom.get("/api/state").json()
    session = next(s for s in env.assistant.sessions.values() if s.user)
    assert (session.chat.fast_model, session.chat.router) == ("claude-haiku-5-5", "auto")


def test_page_language_follows_the_signed_in_person(env, owner):
    assert '<html lang="en">' in owner.get("/").text
    mom, _ = env.member(owner, language="zh")
    assert '<html lang="zh-CN">' in mom.get("/").text
    dad, _ = env.member(owner, name="Dad", language="en")
    assert '<html lang="en">' in dad.get("/").text
    assert '<html lang="en">' in env.client().get("/").text  # signed out


def test_reply_audio_is_only_for_its_own_session(env, owner):
    env.fake_client.queue.append(make_response(text_block("Your secret plan.")))
    lines = owner.post("/api/turn", json={"text": "hi"}).text.splitlines()
    reply = next(json.loads(x) for x in lines if '"reply"' in x)
    mom, _ = env.member(owner)
    assert mom.get(reply["audio"]).status_code == 404
    other_owner_browser = env.client()
    login(other_owner_browser)
    assert other_owner_browser.get(reply["audio"]).status_code == 404
    env.assistant.synthesize = lambda text, session=None, language=None: b"RIFF....WAVE"
    assert owner.get(reply["audio"]).status_code == 200


# --- signing in and out -----------------------------------------------------------

def test_family_sign_out_everywhere_leaves_the_owner_signed_in(env, owner):
    mom, link = env.member(owner)
    mom2 = env.client()
    again = owner.post(f"/api/users/{link['user']['id']}/invite").json()
    assert mom2.post("/api/invite/accept",
                     json={"invite": token_of(again["url"])}).status_code == 200
    assert mom.post("/api/logout-all").status_code == 200
    assert mom.get("/api/state").status_code == 401
    assert mom2.get("/api/state").status_code == 401
    assert owner.get("/api/state").status_code == 200


def test_owner_sign_out_everywhere_keeps_family_signed_in(env, owner):
    mom, _ = env.member(owner)
    other = env.client()
    login(other)
    assert owner.post("/api/logout-all").status_code == 200
    assert other.get("/api/state").status_code == 401
    assert mom.get("/api/state").status_code == 200


def test_owner_sign_in_on_a_family_browser_gets_an_owner_session(env, owner):
    mom, link = env.member(owner)
    family_sid = webapp.read_cookie(TOKEN, mom.cookies.get(webapp.COOKIE),
                                    env.app.state.web.store.epoch)
    login(mom)  # Kyle signs in on that phone
    state = mom.get("/api/state").json()
    assert state["owner"] is True and state["family"] is True
    sid = webapp.read_cookie(TOKEN, mom.cookies.get(webapp.COOKIE),
                             env.app.state.web.store.epoch)
    assert sid != family_sid and env.app.state.web.store.user(sid) is None
    assert env.app.state.web.store.user(family_sid) == link["user"]["id"]


def test_removing_someone_signs_them_out_and_deletes_their_files(env, owner):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    env.fake_client.queue.append(make_response(text_block("好的")))
    assert mom.post("/api/turn", json={"text": "记住这个"}).status_code == 200
    folder = env.tmp / "users" / uid
    assert (folder / "history.sqlite").exists()
    assert delete(owner, f"/api/users/{uid}").status_code == 200
    assert mom.get("/api/state").status_code == 401
    assert not folder.exists()
    assert uid not in (env.tmp / "users" / "users.json").read_text(encoding="utf-8")
    assert uid not in (env.tmp / "sessions.json").read_text(encoding="utf-8")
    assert not any(s.user == uid for s in env.assistant.sessions.values())
    assert delete(owner, f"/api/users/{uid}").status_code == 404
    # a pending invite is removed the same way
    pending = env.invite(owner, name="Later")
    assert delete(owner, f"/api/users/{pending['user']['id']}").status_code == 200
    assert owner.get("/api/users").json()["users"] == []


def test_sessions_file_remembers_who_is_who(tmp_path):
    path = tmp_path / "s.json"
    store = webapp.SessionStore(path)
    owner, mom = store.create(), store.create("u_abcdefghij")
    again = webapp.SessionStore(path)
    assert again.user(owner) is None and again.user(mom) == "u_abcdefghij"
    assert again.revoke_user("u_abcdefghij") == [mom]
    assert not webapp.SessionStore(path).valid(mom)
    assert webapp.SessionStore(path).valid(owner)


# --- prompts and logs -------------------------------------------------------------

def test_english_family_prompts_use_their_name():
    chat = ClaudeChat(client=MagicMock(), tools=None, user_name="Dad")
    system = chat.build_system(["a note"])
    assert "Kyle" not in system
    assert "You are Dad's personal voice assistant" in system
    zh = ClaudeChat(client=MagicMock(), language="zh", user_name="妈妈")
    assert "Kyle" not in zh.build_system(["a note"])
    kyle = ClaudeChat(client=MagicMock())
    assert "You are Kyle's personal voice assistant" in kyle.build_system()


def test_family_time_zone_is_used_for_the_date():
    chat = ClaudeChat(client=MagicMock(), timezone="Asia/Shanghai")
    assert "UTC+0800" in chat.build_system()


def test_no_names_tokens_or_links_at_info(env, owner, caplog):
    with caplog.at_level(logging.DEBUG):
        mom, link = env.member(owner, name="Zebediah")
        owner.post(f"/api/users/{link['user']['id']}/invite")
        env.client().post("/api/invite/accept", json={"invite": "hcinv.wrongwrong"})
        delete(owner, f"/api/users/{link['user']['id']}")
    info = [r for r in caplog.records if r.levelno >= logging.INFO]
    text = "\n".join(r.getMessage() for r in info)
    assert "Zebediah" not in text
    assert "hcinv." not in text and "#invite=" not in text
    assert link["user"]["id"] in text  # ids are fine


def test_invite_tokens_and_links_are_redacted():
    token = "hcinv." + "A" * 43
    assert token not in redact(f"accept {token}")
    assert "AAAA" not in redact(f"https://x.onrender.com/#invite={token}")


def test_family_defaults():
    from assistant.config import SERVER_PROFILE

    assert DEFAULTS["family"]["enabled"] is False
    assert DEFAULTS["family"]["invite_days"] == 30
    assert DEFAULTS["family"]["max_users"] == 10
    assert SERVER_PROFILE["family"] == {"enabled": True, "dir": "/data/users"}


def test_removal_holds_the_lock_while_signing_out(tmp_path, fake_client):
    """redeem() and remove() share one lock: sign-out runs inside it, so an
    invite can't be accepted between the entry going and the sign-out."""
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    fam = family_mod.Family(config, client=fake_client)
    user, token = fam.invite("Mom")
    seen = []
    fam.remove(user["id"], lambda uid: seen.append(fam.lock._is_owned()))
    assert seen == [True]
    assert fam.redeem(token, lambda uid: "sid") is None
    assert not os.path.exists(tmp_path / "users" / user["id"])


# --- round 2: fail closed, sign-out scope, failed saves, the link's origin -------

def sid_of(client, env) -> str:
    return webapp.read_cookie(TOKEN, client.cookies.get(webapp.COOKIE),
                              env.app.state.web.store.epoch)


def remove_now(env, uid):
    """What DELETE /api/users/{id} does, called from inside a request."""
    store = env.app.state.web.store
    env.family.remove(uid, lambda u: (store.revoke_user(u), env.assistant.drop_user(u)))


def test_a_member_removed_mid_request_is_never_the_owner(env, owner):
    """Remove lands between the cookie check (store.valid) and the session
    lookup: the member gets nothing, never Kyle's session."""
    mom, link = env.member(owner)
    dad, dad_link = env.member(owner, name="Dad", language="en")
    store = env.app.state.web.store
    valid = store.valid
    targets = {sid_of(mom, env): link["user"]["id"],
               sid_of(dad, env): dad_link["user"]["id"]}

    def valid_then_removed(sid):
        ok = valid(sid)
        if ok and sid in targets:
            remove_now(env, targets.pop(sid))
        return ok

    store.valid = valid_then_removed
    r = mom.get("/api/state")
    assert r.status_code == 401, r.text
    assert not any(s.user is None for s in env.assistant.sessions.values()
                   if s.sid in (sid_of(mom, env), sid_of(dad, env)))
    # "sign out everywhere" in the same gap must not sign Kyle out
    assert dad.post("/api/logout-all").status_code == 401
    store.valid = valid
    assert owner.get("/api/state").json()["owner"] is True


def test_unlisted_sessions_are_nobodys(env, owner):
    web = env.app.state.web
    assert web.store.user("never-listed") == webapp.GONE
    assert web.store.user(None) == webapp.GONE
    assert env.assistant.session("never-listed") is None
    assert web.user_of(web.bearer_sid) is None  # WEB_TOKEN itself: the owner
    assert web.user_of(sid_of(owner, env)) is None


def test_a_session_made_while_its_member_is_removed_is_dropped(env, owner):
    mom, link = env.member(owner)
    uid, sid = link["user"]["id"], sid_of(mom, env)
    context = env.family.context

    def context_then_removed(u):
        ctx = context(u)  # made for them; then Remove (and drop_user) runs
        remove_now(env, u)
        return ctx

    env.family.context = context_then_removed
    assert env.assistant.session(sid) is None
    assert sid not in env.assistant.sessions
    assert env.family.get(uid) is None


def test_owner_sign_out_everywhere_keeps_family_voice_calls(env, owner):
    from tests.test_webapp import FakeVoiceConversation

    mom, _ = env.member(owner)
    other = env.client()
    login(other)
    calls = {sid_of(mom, env): FakeVoiceConversation(),
             sid_of(other, env): FakeVoiceConversation(),
             env.app.state.web.bearer_sid: FakeVoiceConversation()}
    env.app.state.voice_conversations.update({k: (v, None) for k, v in calls.items()})
    assert owner.post("/api/logout-all").status_code == 200
    assert calls[sid_of(mom, env)].ended == []
    assert calls[sid_of(other, env)].ended == ["signed_out"]
    assert calls[env.app.state.web.bearer_sid].ended == ["signed_out"]
    assert mom.get("/api/state").status_code == 200


def failing_saves(monkeypatch):
    from assistant.tools import google_auth

    def fail(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(google_auth, "write_private", fail)


def test_a_failed_save_changes_nothing_and_remove_can_be_retried(env, owner, monkeypatch):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    env.fake_client.queue.append(make_response(text_block("好的")))
    assert mom.post("/api/turn", json={"text": "你好"}).status_code == 200
    folder = env.tmp / "users" / uid
    with monkeypatch.context() as m:
        failing_saves(m)
        assert delete(owner, f"/api/users/{uid}").status_code == 500
        assert env.family.get(uid) is not None and folder.exists()
        with pytest.raises(OSError):
            env.family.invite("Eve")
        with pytest.raises(OSError):
            env.family.reinvite(uid)
    assert [u["id"] for u in env.family.members()] == [uid]
    assert env.family.get(uid)["invite_hash"] is None  # reinvite didn't stick
    assert delete(owner, f"/api/users/{uid}").status_code == 200  # the retry works
    assert not folder.exists() and env.family.members() == []


def test_a_failed_save_while_accepting_leaves_the_invite_usable(env, owner, monkeypatch):
    link = env.invite(owner)
    with monkeypatch.context() as m:
        failing_saves(m)
        with pytest.raises(OSError):
            env.family.redeem(token_of(link["url"]), lambda uid: "sid")
    assert env.family.get(link["user"]["id"])["status"] == "invited"
    assert env.family.redeem(token_of(link["url"]), lambda uid: "sid") == "sid"


def test_a_folder_that_cant_be_deleted_is_retried(env, owner, monkeypatch, caplog):
    mom, link = env.member(owner, name="Zebediah")
    uid = link["user"]["id"]
    env.fake_client.queue.append(make_response(text_block("好的")))
    assert mom.post("/api/turn", json={"text": "你好"}).status_code == 200
    folder = env.tmp / "users" / uid
    with monkeypatch.context() as m:
        m.setattr(family_mod.time, "sleep", lambda s: None)
        m.setattr(family_mod.shutil, "rmtree", MagicMock(side_effect=PermissionError("locked")))
        with caplog.at_level(logging.INFO):
            assert delete(owner, f"/api/users/{uid}").status_code == 200
        assert folder.exists()
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert f"{uid}'s folder couldn't be deleted yet" in logged
        assert "Zebediah" not in logged  # ids only
        assert env.family.members() == []  # gone from the list; files pending
    saved = json.loads((env.tmp / "users" / "users.json").read_text(encoding="utf-8"))
    assert saved["deleting"] == [uid] and uid not in saved["users"]
    # the next start (or the owner opening the list) finishes the job
    again = family_mod.Family(env.config, client=env.fake_client)
    assert not folder.exists()
    saved = json.loads((env.tmp / "users" / "users.json").read_text(encoding="utf-8"))
    assert saved["deleting"] == []
    again.close()


@pytest.mark.parametrize("failover,expected", [
    ({"role": "cloud", "app_origin": "https://hey.example.com"}, "https://hey.example.com/"),
    ({"role": "cloud", "app_origin": "https://app.example.com",
      "backends": [{"role": "cloud", "url": "https://cloud.example.com"}]},
     "https://cloud.example.com/"),
    ({"role": ""}, "https://testserver/"),
])
def test_invite_links_point_at_this_server(env, owner, monkeypatch, failover, expected):
    """Never the page's Origin: with failover the owner's page may be the
    Pi's (or the app origin) calling the cloud cross-origin."""
    monkeypatch.delenv("FAILOVER_BACKENDS", raising=False)
    env.config["failover"].update(failover)
    r = owner.post("/api/invites", json={"name": "Mom"})
    assert r.json()["url"].startswith(expected + "#invite=hcinv."), r.json()["url"]
    env.config["webapp"]["public_origin"] = "https://public.example.com"
    r = owner.post("/api/invites", json={"name": "Dad"})
    assert r.json()["url"].startswith("https://public.example.com/#invite=")


def test_the_pc_hub_is_the_owners_only(env, tmp_path):
    """Merge of family accounts with the PC hub (/devteam): a family member
    signed in on the PC's loopback web app must not reach Kyle's coordinator
    sessions (require_owner, 404 as if absent)."""
    from assistant import devhub
    from tests.test_devhub import LOOPBACK, SID, make_hub

    devhub.install(env.app, make_hub(tmp_path))
    owner = TestClient(env.app, base_url=BASE, client=LOOPBACK)
    login(owner)
    link = env.invite(owner)
    mom = TestClient(env.app, base_url=BASE, client=LOOPBACK)
    assert mom.post("/api/invite/accept",
                    json={"invite": token_of(link["url"])}).status_code == 200
    assert mom.get("/api/devteam/state").status_code == 404
    for path in ("/api/devteam/send", "/api/devteam/clear"):
        r = mom.post(path, json={"session": SID, "text": "x"})
        assert r.status_code == 404, path
    assert owner.get("/api/devteam/state").status_code == 200


# --- tiers (public beta piece G1) -----------------------------------------------

def test_an_invite_defaults_to_beta_and_the_owner_can_pick_family(env, owner):
    beta = env.invite(owner, name="Tester")
    assert beta["user"]["tier"] == "beta"
    fam = env.invite(owner, name="Dad", tier="family")
    assert fam["user"]["tier"] == "family"
    r = owner.post("/api/invites", json={"name": "Eve", "tier": "admin"})
    assert r.status_code == 400 and "tier" in r.json()["error"]
    listed = {u["name"]: u["tier"] for u in owner.get("/api/users").json()["users"]}
    assert listed == {"Tester": "beta", "Dad": "family"}
    saved = json.loads(env.family.path.read_text())["users"]
    assert sorted(u["tier"] for u in saved.values()) == ["beta", "family"]
    # the Python API defaults to beta too
    user, _ = env.family.invite("Later")
    assert user["tier"] == "beta"


def test_people_listed_before_tiers_load_as_family(tmp_path, fake_client):
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    (tmp_path / "users").mkdir()
    old = {"id": "u_oldmember1", "name": "Mom", "language": "zh", "timezone": "",
           "created": 1, "status": "active", "invite_hash": None,
           "invite_expires": None}
    odd = {**old, "id": "u_oddmember1", "name": "Odd", "tier": "superuser"}
    (tmp_path / "users" / "users.json").write_text(json.dumps(
        {"users": {old["id"]: old, odd["id"]: odd}, "deleting": []}))
    fam = family_mod.Family(config, client=fake_client)
    try:
        assert fam.get("u_oldmember1")["tier"] == "family"
        assert fam.tier("u_oldmember1") == "family"
        assert fam.context("u_oldmember1").tier == "family"
        # an unexpected value is the narrower tier, never family
        assert fam.tier("u_oddmember1") == "beta"
        assert {u["id"]: u["tier"] for u in fam.members()} == {
            "u_oldmember1": "family", "u_oddmember1": "beta"}
    finally:
        fam.close()


def test_each_members_history_uses_max_history_mb(env, owner):
    assert DEFAULTS["family"]["max_history_mb"] == 20
    assert env.config["history"]["max_mb"] == 500  # the owner's, not theirs
    _, link = env.member(owner)
    assert env.family.context(link["user"]["id"]).history.max_mb == 20
    env.config["family"]["max_history_mb"] = 7
    fam = family_mod.Family(env.config, client=env.fake_client)
    try:
        assert fam.context(link["user"]["id"]).history.max_mb == 7
    finally:
        fam.close()


def test_the_tier_is_logged_never_the_name(env, owner, caplog):
    with caplog.at_level(logging.INFO, logger="assistant.family"):
        link = env.invite(owner, name="Zebediah")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert link["user"]["id"] in text and "beta" in text
    assert "Zebediah" not in text
