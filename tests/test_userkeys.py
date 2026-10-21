"""Bring your own Anthropic key (public beta piece G2): assistant/userkeys.py,
userkeys_web.py, and family.Family's per-person client.

All offline. Each person's own client is a mocked Anthropic client made per
key (Family's `user_client` seam); the key check (userkeys.check_key) is
replaced, or run against the real SDK over an httpx MockTransport."""

import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import family as family_mod, i18n, usage, userkeys, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.cli import friendly_error, get_reply  # noqa: E402
from assistant.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS, load_config  # noqa: E402
from assistant.history import TITLE_PROMPT, HistoryStore, Recorder  # noqa: E402
from assistant.tools import ToolRegistry  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, make_usage, text_block  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, events, login  # noqa: E402

SECRET = "s" * 48                      # USER_DATA_KEY
KEY_A = "sk-ant-api03-" + "A" * 80 + "wxyz"
KEY_B = "sk-ant-api03-" + "B" * 80 + "1234"
URL = "/api/me/anthropic-key"
REAL_ANTHROPIC = anthropic.Anthropic


def fake_api(key=None):
    """A mocked anthropic.Anthropic: messages.create/stream answer "Hi."
    (with usage) unless something is queued; with_options is itself."""
    c = MagicMock()
    c.key, c.queue, c.calls = key, [], []

    def create(**kwargs):
        c.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        return c.queue.pop(0) if c.queue else make_response(
            text_block("Hi."), usage=make_usage(1000, 100))

    class Stream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def text_stream(self):
            yield from (b.text for b in self.response.content if b.type == "text")

        def get_final_message(self):
            return self.response

    c.messages.create.side_effect = create
    c.messages.stream.side_effect = lambda **kw: Stream(create(**kw))
    c.with_options.return_value = c
    return c


class Env(SimpleNamespace):
    def client(self):
        return TestClient(self.app, base_url=BASE)

    def member(self, owner, name="Tess", tier="beta", language="en"):
        r = owner.post("/api/invites", json={"name": name, "language": language,
                                             "tier": tier})
        assert r.status_code == 200, r.text
        c = self.client()
        token = r.json()["url"].split("#invite=", 1)[1]
        assert c.post("/api/invite/accept", json={"invite": token}).status_code == 200
        return c, r.json()["user"]["id"]

    def reply(self, c, text="hello"):
        r = c.post("/api/turn", json={"text": text})
        found = [e for e in events(r) if e["type"] in ("reply", "error")]
        return found[0]["text"] if found[0]["type"] == "reply" else found[0]["message"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    owner_api = fake_api("owner")
    made: list = []

    def user_client(key):
        made.append(fake_api(key))
        return made[-1]

    fam = family_mod.Family(config, client=owner_api, user_client=user_client)
    owner_store = HistoryStore(tmp_path / "owner.sqlite", eventlog=False)

    def chat_factory(registry):
        return ClaudeChat(client=owner_api, model="test-model", tools=registry,
                          recorder=Recorder(owner_store, "web", run_async=False,
                                            client_fn=lambda: owner_api))

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(),
                                    tools=ToolRegistry(), clock=Clock(),
                                    history=owner_store, family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")
    checked: list = []
    monkeypatch.setattr(userkeys, "check_key", lambda key: checked.append(key))
    e = Env(app=app, fam=fam, owner_api=owner_api, made=made, checked=checked,
            config=config, tmp=tmp_path, assistant=assistant)
    yield e
    fam.close()
    owner_store.close()


@pytest.fixture
def owner(env):
    c = env.client()
    login(c)
    return c


def put(c, key):
    return c.put(URL, json={"key": key})


def delete(c):
    return c.request("DELETE", URL, content=b"{}",
                     headers={"Content-Type": "application/json"})


def by_key(env, key):
    return [c for c in env.made if c.key == key]


# --- the key store ------------------------------------------------------------------

def test_a_saved_key_is_encrypted_and_opens_again(tmp_path):
    store = userkeys.KeyStore(tmp_path, environ={"USER_DATA_KEY": SECRET})
    store.save("u_abcdefgh1", KEY_A)
    raw = store.path("u_abcdefgh1").read_bytes()
    assert KEY_A.encode() not in raw and b"AAAAAAAA" not in raw and b"wxyz" not in raw
    assert store.load("u_abcdefgh1") == KEY_A
    # copied into someone else's folder, it isn't theirs
    other = store.path("u_other00001")
    other.parent.mkdir(parents=True)
    other.write_bytes(raw)
    assert store.load("u_other00001") is None
    # another purpose's label (e.g. Notion tokens later) doesn't open it
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        userkeys.fernet(b"hey-claude/notion-token/v1", {"USER_DATA_KEY": SECRET}).decrypt(raw)


@pytest.mark.parametrize("environ", [{}, {"USER_DATA_KEY": ""},
                                     {"USER_DATA_KEY": "short-secret"}])
def test_without_a_usable_user_data_key_nothing_is_stored(tmp_path, environ):
    store = userkeys.KeyStore(tmp_path, environ=environ)
    with pytest.raises(userkeys.KeyProblem) as e:
        store.save("u_abcdefgh1", KEY_A)
    assert e.value.status == 503 and "USER_DATA_KEY" in str(e.value)
    # and says which: missing, or too short to be a real secret
    assert ("isn't set" if not environ.get("USER_DATA_KEY") else "too short") in str(e.value)
    assert KEY_A not in str(e.value)
    assert not store.path("u_abcdefgh1").exists()
    assert list(tmp_path.iterdir()) == []


def test_a_new_user_data_key_means_no_key_and_no_crash(tmp_path, caplog):
    userkeys.KeyStore(tmp_path, environ={"USER_DATA_KEY": SECRET}).save("u_abcdefgh1", KEY_A)
    rotated = userkeys.KeyStore(tmp_path, environ={"USER_DATA_KEY": "r" * 48})
    with caplog.at_level(logging.DEBUG):
        assert rotated.load("u_abcdefgh1") is None
    assert rotated.stored("u_abcdefgh1")  # Settings says "enter it again"
    assert "enter it again" in caplog.text and KEY_A not in caplog.text


def test_the_key_shape_is_checked_before_anything_else():
    for bad in (None, 42, "", "hello", "sk-ant-", "sk-ant-short", "sk-ant-" + "x" * 300,
                "sk-ant-api03-has space-" + "x" * 20, "sk-proj-" + "x" * 40):
        with pytest.raises(userkeys.KeyProblem) as e:
            userkeys.clean_key(bad)
        assert e.value.status == 400
    assert userkeys.clean_key(f"  {KEY_A}\n") == KEY_A


def test_user_data_key_is_a_secret_kept_from_other_instances():
    assert "USER_DATA_KEY" in SECRET_ENV_VARS and "USER_DATA_KEY" in ISOLATED_ENV_VARS


# --- the check, against the real SDK (no network: an httpx MockTransport) ------------

def sdk_answering(monkeypatch, handler):
    """anthropic.Anthropic as check_key makes it, its requests answered by
    `handler` (real SDK errors, no retries)."""
    seen = []

    def make(**kwargs):
        def respond(request):
            seen.append(request)
            return handler(request, http)

        # newer SDKs are built on httpx2, older ones on httpx
        for name in ("httpx2", "httpx"):
            try:
                http = __import__(name)
                return REAL_ANTHROPIC(**{**kwargs, "max_retries": 0},
                            http_client=http.Client(transport=http.MockTransport(respond)))
            except (ImportError, TypeError):
                continue
        raise AssertionError("no HTTP library the SDK accepts")

    monkeypatch.setattr(anthropic, "Anthropic", make)
    return seen


def test_check_key_maps_the_apis_answers(monkeypatch):
    ok = {"data": [{"id": "claude-sonnet-5-5", "type": "model", "display_name": "x",
                    "created_at": "2026-01-01T00:00:00Z"}],
          "has_more": False, "first_id": "claude-sonnet-5-5", "last_id": "claude-sonnet-5-5"}
    seen = sdk_answering(monkeypatch, lambda r, http: http.Response(200, json=ok))
    userkeys.check_key(KEY_A)
    assert seen[0].headers["x-api-key"] == KEY_A and seen[0].url.path == "/v1/models"
    error = {"type": "error", "error": {"type": "authentication_error",
                                        "message": "invalid x-api-key"}}
    for status, expected in ((401, 400), (403, 400), (404, 400), (500, 503), (529, 503),
                             (429, 503)):
        sdk_answering(monkeypatch, lambda r, http, s=status: http.Response(s, json=error))
        with pytest.raises(userkeys.KeyProblem) as e:
            userkeys.check_key(KEY_A)
        assert e.value.status == expected and KEY_A not in str(e.value)
        assert e.value.__cause__ is None and e.value.__suppress_context__

    def down(request, http):
        raise http.ConnectError("no route", request=request)

    sdk_answering(monkeypatch, down)
    with pytest.raises(userkeys.KeyProblem) as e:
        userkeys.check_key(KEY_A)
    assert e.value.status == 503


def test_a_persons_client_never_carries_the_servers_credentials(monkeypatch, tmp_path):
    """Their client is made with an explicit api_key: Kyle's ANTHROPIC_API_KEY
    and ANTHROPIC_AUTH_TOKEN in the server's environment are not sent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-owner-" + "o" * 30)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "owner-bearer-token")
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    client = family_mod.Family(config)._new_user_client(KEY_A)
    assert client.api_key == KEY_A and client.auth_token is None
    assert client.auth_headers == {"X-Api-Key": KEY_A}
    a = config["assistant"]
    assert (client.max_retries, client.timeout) == (a["api_max_retries"], a["api_timeout"])



def test_an_older_sdk_that_reads_the_bearer_token_anyway_is_cleared(monkeypatch, tmp_path):
    """anthropic < 0.98 read ANTHROPIC_AUTH_TOKEN even with an explicit
    api_key and sent it next to X-Api-Key. Both a person's chat client and
    the key check must drop it."""
    import anthropic

    class OldSdk(anthropic.Anthropic):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "owner-bearer-token")
    monkeypatch.setattr(anthropic, "Anthropic", OldSdk)
    assert "Authorization" in OldSdk(api_key=KEY_A).auth_headers  # the old behaviour
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    for client in (family_mod.Family(config)._new_user_client(KEY_A),
                   userkeys.user_client(KEY_A, timeout=10, max_retries=1)):
        assert client.auth_token is None and client.auth_headers == {"X-Api-Key": KEY_A}

# --- whose client each conversation uses -------------------------------------------

def test_two_people_get_their_own_clients_for_chats_and_titles(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    ugo, ugo_id = env.member(owner, "Ugo")
    assert put(tess, KEY_A).status_code == 200
    assert put(ugo, KEY_B).status_code == 200
    assert env.reply(tess) == "Hi." and env.reply(ugo) == "Hi."
    (a,), (b,) = by_key(env, KEY_A), by_key(env, KEY_B)
    assert len(a.calls) == 1 and len(b.calls) == 1
    assert env.owner_api.calls == []
    # the Recorder's title call is on the same person's key
    ctx = env.fam.context(tess_id)
    chat = ctx.chat_factory(ToolRegistry())
    a.queue.append(make_response(text_block("Hello."), usage=make_usage(10, 5)))
    chat.ask("hi there")
    a.queue.append(make_response(text_block('{"title": "Greeting", "summary": "Said hi."}'),
                                 usage=make_usage(10, 5)))
    chat.end_conversation()
    chat.recorder.flush()
    assert any(call.get("system") == TITLE_PROMPT for call in a.calls)
    assert not any(call.get("system") == TITLE_PROMPT for call in b.calls)
    assert env.owner_api.calls == []
    assert env.fam.client(tess_id) is a and env.fam.client(ugo_id) is b


def test_a_beta_tester_without_a_key_is_told_and_nothing_is_sent(env, owner, caplog):
    tess, tess_id = env.member(owner, "Tess", language="zh")
    with caplog.at_level(logging.DEBUG):
        said = env.reply(tess, "你好")
    assert said == i18n.t("no_key", "zh") == "请先在设置里添加你的 Anthropic API 密钥。"
    assert env.owner_api.calls == [] and env.made == []
    assert env.owner_api.messages.stream.call_count == 0
    assert env.owner_api.messages.create.call_count == 0
    with pytest.raises(userkeys.MissingKey):
        env.fam.client(tess_id)
    # nothing half-saved: the turn was rolled back
    session = next(s for s in env.assistant.sessions.values() if s.user == tess_id)
    assert session.chat.history == []
    assert "你好" not in caplog.text


def test_a_family_member_without_a_key_uses_the_owners(env, owner):
    mom, mom_id = env.member(owner, "Mom", tier="family")
    assert env.reply(mom) == "Hi."
    assert len(env.owner_api.calls) == 1 and env.made == []
    assert env.fam.api_for(mom_id) == (env.owner_api, False)
    assert usage.month_usd() > 0  # the owner's key, the owner's bill
    # with a key of her own, her next turn is on it
    assert put(mom, KEY_A).status_code == 200
    assert env.reply(mom) == "Hi."
    assert len(by_key(env, KEY_A)[0].calls) == 1 and len(env.owner_api.calls) == 1


def test_removing_or_replacing_a_key_applies_to_the_next_call(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    assert put(tess, KEY_A).status_code == 200
    assert env.reply(tess) == "Hi."  # this conversation stays open throughout
    assert put(tess, KEY_B).status_code == 200
    assert env.reply(tess) == "Hi."
    (a,), (b,) = by_key(env, KEY_A), by_key(env, KEY_B)
    assert len(b.calls) == 1 and len(a.calls) == 1
    r = delete(tess)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not env.fam.keys.stored(tess_id)
    assert env.reply(tess) == i18n.t("no_key")
    assert len(b.calls) == 1 and env.owner_api.calls == []
    # a family member falls back to the owner's again
    mom, mom_id = env.member(owner, "Mom", tier="family")
    put(mom, KEY_A)
    assert env.fam.api_for(mom_id)[1] is True
    delete(mom)
    assert env.fam.api_for(mom_id) == (env.owner_api, False)


def test_someone_removed_never_falls_back_to_the_owners_key(env, owner):
    mom, mom_id = env.member(owner, "Mom", tier="family")
    api = family_mod.UserApi(env.fam, mom_id)
    assert api.resolve() is env.owner_api
    assert owner.post(f"/api/users/{mom_id}/remove", json={}).status_code == 200
    with pytest.raises(userkeys.MissingKey):
        api.resolve()
    assert api.client_or_none() is None


def test_a_rotated_user_data_key_asks_for_the_key_again(env, owner, monkeypatch):
    tess, tess_id = env.member(owner, "Tess")
    assert put(tess, KEY_A).status_code == 200
    monkeypatch.setenv("USER_DATA_KEY", "n" * 48)
    env.fam._user_clients.clear()  # as after the restart that changed it
    st = tess.get(URL).json()
    assert st["set"] is False and st["unreadable"] is True and st["last4"] is None
    assert env.reply(tess) == i18n.t("no_key")
    assert put(tess, KEY_B).status_code == 200  # entering it again works
    assert tess.get(URL).json()["set"] is True


def test_a_rejected_own_key_is_theirs_to_fix_not_kyles(env, owner):
    tess, _ = env.member(owner, "Tess")
    put(tess, KEY_A)
    env.reply(tess)  # makes their client
    a = by_key(env, KEY_A)[0]
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    a.messages.stream.side_effect = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    assert env.reply(tess) == i18n.t("bad_user_key")
    # the owner's own key failing still says "my key"
    env.owner_api.messages.stream.side_effect = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    assert env.reply(owner) == i18n.t("bad_key")


def test_a_turn_with_a_file_says_the_same(env, owner):
    tess, _ = env.member(owner, "Tess")
    note = [("attachment", ("note.txt", b"buy milk", "text/plain"))]

    def said():
        r = tess.post("/api/turn", data={"text": "what's this"}, files=note)
        return next(e for e in events(r) if e["type"] == "reply")["text"]

    assert said() == i18n.t("no_key")
    put(tess, KEY_A)
    env.reply(tess)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    by_key(env, KEY_A)[0].messages.stream.side_effect = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    assert said() == i18n.t("bad_user_key")
    assert env.owner_api.calls == []


# --- the bill ----------------------------------------------------------------------

def test_their_calls_stay_out_of_the_owners_month_usd(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    put(tess, KEY_A)
    assert env.reply(tess) == "Hi."
    ctx = env.fam.context(tess_id)
    chat = ctx.chat_factory(ToolRegistry())
    chat.ask("hi")
    chat.end_conversation()
    chat.recorder.flush()  # a title call on their key too
    assert usage.month_usd() == 0
    assert owner.get("/api/state").json()["month_usd"] == 0
    theirs = env.fam.meter(tess_id)
    assert theirs.month_usd() > 0
    assert sum(m["calls"] for m in theirs.months[next(iter(theirs.months))].values()) >= 3
    assert (env.tmp / "users" / tess_id / "usage.json").exists()
    assert tess.get(URL).json()["month_usd"] == theirs.month_usd()
    assert "month_usd" not in tess.get("/api/state").json()


def test_usage_after_removal_writes_nothing(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    put(tess, KEY_A)
    owner.post(f"/api/users/{tess_id}/remove", json={})
    env.fam.record_usage(tess_id, "claude-sonnet-5-5", make_usage(10, 10))
    assert not (env.tmp / "users" / tess_id).exists()


# --- the routes ----------------------------------------------------------------------

def test_put_get_delete_and_the_key_never_comes_back(env, owner, caplog):
    tess, tess_id = env.member(owner, "Tess")
    st = tess.get(URL).json()
    assert st == {"set": False, "last4": None, "unreadable": False, "required": True,
                  "available": True}
    assert tess.get("/api/state").json()["api_key"] == "required"
    with caplog.at_level(logging.DEBUG):
        r = put(tess, f" {KEY_A} ")
        assert r.status_code == 200 and r.json() == {"set": True, "last4": "wxyz"}
        assert env.checked == [KEY_A]
        got = tess.get(URL)
        assert got.json()["set"] is True and got.json()["last4"] == "wxyz"
        assert env.reply(tess) == "Hi."
        assert delete(tess).status_code == 200
    users = (env.tmp / "users" / "users.json").read_text()
    for text in (r.text, got.text, users, caplog.text):
        assert KEY_A not in text and "A" * 20 not in text
    assert tess_id in caplog.text  # ids are logged
    assert tess.get(URL).json()["set"] is False


def test_bad_keys_get_400_and_a_failed_check_503(env, owner, monkeypatch):
    tess, tess_id = env.member(owner, "Tess")
    r = put(tess, "sk-proj-" + "x" * 40)
    assert r.status_code == 400 and env.checked == []  # no call for a malformed key
    assert put(tess, None).status_code == 400
    assert tess.put(URL, content=b"not json",
                    headers={"Content-Type": "application/json"}).status_code == 400

    def refused(key):
        raise userkeys.KeyProblem("Anthropic didn't accept that key")

    monkeypatch.setattr(userkeys, "check_key", refused)
    r = put(tess, KEY_A)
    assert r.status_code == 400 and r.json()["error"] == "Anthropic didn't accept that key"

    def offline(key):
        raise userkeys.KeyProblem("couldn't reach Anthropic to check the key; try again", 503)

    monkeypatch.setattr(userkeys, "check_key", offline)
    assert put(tess, KEY_B).status_code == 503
    assert not env.fam.keys.stored(tess_id)


def test_no_user_data_key_refuses_to_store_and_says_why(env, owner, monkeypatch):
    monkeypatch.delenv("USER_DATA_KEY")
    tess, tess_id = env.member(owner, "Tess")
    r = put(tess, KEY_A)
    assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert env.checked == []  # not even checked
    assert not env.fam.keys.stored(tess_id)
    assert tess.get(URL).json()["available"] is False


def test_only_the_signed_in_person_and_never_the_owner(env, owner):
    assert owner.get(URL).status_code == 404
    assert put(owner, KEY_A).status_code == 404 and env.checked == []
    assert delete(owner).status_code == 404
    assert "api_key" not in owner.get("/api/state").json()
    anon = env.client()
    assert anon.get(URL).status_code == 401 and put(anon, KEY_A).status_code == 401
    # Tess's key is hers only
    tess, tess_id = env.member(owner, "Tess")
    ugo, ugo_id = env.member(owner, "Ugo", tier="family")
    put(tess, KEY_A)
    assert ugo.get(URL).json()["set"] is False
    assert ugo.get("/api/state").json()["api_key"] == "optional"
    delete(ugo)
    assert tess.get(URL).json()["set"] is True


def test_put_is_rate_limited_per_person(env, owner):
    tess, _ = env.member(owner, "Tess")
    ugo, _ = env.member(owner, "Ugo")
    codes = [put(tess, KEY_A).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    assert len(env.checked) == 5
    assert put(ugo, KEY_B).status_code == 200  # someone else isn't held up


# --- errors and the voice path --------------------------------------------------------

def test_friendly_error_maps_missing_and_rejected_own_keys():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rejected = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=request), body=None)
    assert friendly_error(userkeys.MissingKey(), output_fn=lambda m: None) == i18n.t("no_key")
    assert friendly_error(rejected, output_fn=lambda m: None, own_key=True) == \
        i18n.t("bad_user_key")
    assert friendly_error(rejected, output_fn=lambda m: None) == i18n.t("bad_key")
    with i18n.using("zh"):
        assert friendly_error(userkeys.MissingKey(), output_fn=lambda m: None) == \
            i18n.ZH["no_key"]


def test_the_new_sentences_exist_in_both_languages():
    for key in ("no_key", "bad_user_key"):
        assert i18n.EN[key] and i18n.ZH[key] and i18n.EN[key] != i18n.ZH[key]
    assert "my" not in i18n.EN["bad_user_key"] and "Settings" in i18n.EN["no_key"]


def test_the_voice_path_says_no_key():
    import numpy as np

    from assistant.voice_session import ChatBackend, VoiceConversation
    from tests.voice_fakes import Events, FakeSynth, FakeTranscriber as Heard

    class NoKeyBackend(ChatBackend):
        def ask(self, text, on_text, on_tool, cancel):
            raise userkeys.MissingKey()

    for lang in ("en", "zh"):
        sent = Events()
        conv = VoiceConversation(NoKeyBackend(ClaudeChat(client=MagicMock(), model="m")),
                                 Heard("hello"), FakeSynth(), sent, language=lang)
        conv.utterance(1, np.zeros(16000, dtype=np.float32))
        sent.wait_for("turn_done")
        assert sent.of("error")[0]["message"] == i18n.t("no_key", lang)
        conv.end("closed")


def test_get_reply_on_a_keyless_chat_never_calls_anyone(env, owner):
    _, tess_id = env.member(owner, "Tess")
    chat = env.fam.context(tess_id).chat_factory(ToolRegistry())
    assert get_reply(chat, "hi", output_fn=lambda m: None) == i18n.t("no_key")
    assert chat.history == [] and env.owner_api.calls == []


def test_the_key_never_reaches_the_log_at_any_level(env, owner, caplog, monkeypatch):
    tess, tess_id = env.member(owner, "Tess")
    with caplog.at_level(logging.DEBUG):
        put(tess, KEY_A)
        env.reply(tess)
        a = by_key(env, KEY_A)[0]
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        a.messages.stream.side_effect = anthropic.AuthenticationError(
            "invalid x-api-key", response=httpx.Response(401, request=request), body=None)
        env.reply(tess)
        tess.get(URL)
        monkeypatch.setenv("USER_DATA_KEY", "z" * 48)
        env.fam._user_clients.clear()
        env.reply(tess)
        delete(tess)
    assert caplog.records
    for record in caplog.records:
        assert KEY_A not in record.getMessage()
        assert KEY_A not in str(record.args) and KEY_A not in (record.exc_text or "")
    assert KEY_A not in caplog.text
