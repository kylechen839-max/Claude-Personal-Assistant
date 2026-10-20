"""Family invites, piece B: each family member's own Google account and
speech language on the shared cloud server (family.py, google_web.py,
webapp.SpeechByLanguage, i18n.using).

Offline: Google's token endpoint (Flow.fetch_token) and API client
(googleapiclient.discovery.build) are mocked, as in test_google_web.py;
Whisper and the voices are fakes. Kyle's own Google sign-in is set in
GOOGLE_TOKEN_JSON throughout (as on Render) and must never reach family."""

import json
import logging
import threading
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("google_auth_oauthlib")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import family as family_mod, google_web, i18n, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import load_config  # noqa: E402
from tests.conftest import make_response, text_block, tool_use_block  # noqa: E402
from tests.test_family import OWNER_TOOLS, owner_registry  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, login, wav_bytes  # noqa: E402

ORIGIN = "https://hey-claude.example.com"
WEB_CLIENT = {"web": {
    "client_id": "family.apps.googleusercontent.com", "client_secret": "fam-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": [ORIGIN + "/google/callback"]}}
SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]


def token_of(who: str) -> dict:
    """An authorized-user token as Flow.credentials.to_json() writes it."""
    return {"token": f"ya29.{who}-access", "refresh_token": f"1//{who}-refresh-secret",
            "client_id": "family.apps.googleusercontent.com",
            "client_secret": "fam-secret",
            "token_uri": "https://oauth2.googleapis.com/token", "scopes": SCOPES,
            "expiry": "2099-01-01T00:00:00Z"}  # valid: nothing to refresh


KYLE = token_of("kyle")


@pytest.fixture(autouse=True)
def no_google_network(monkeypatch):
    """Nothing here may reach Google (a refresh would)."""
    from google.oauth2.credentials import Credentials

    def refresh(self, request):
        raise AssertionError("a test tried to refresh a Google token")

    monkeypatch.setattr(Credentials, "refresh", refresh)


class Whisper:
    """A fake Transcriber: says `text`, counts what it heard."""

    def __init__(self, text):
        self.text = text
        self.heard = 0

    def transcribe(self, recording):
        self.heard += 1
        return self.text


class Voice:
    """A fake Piper voice (fast_wav renders anything with synthesize())."""

    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return np.zeros(1600, dtype=np.float32), 16000


@pytest.fixture
def g(fake_client, tmp_path, monkeypatch):
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["webapp"]["public_origin"] = ORIGIN
    config["calendar"]["token"] = str(tmp_path / "kyle_token.json")
    monkeypatch.setenv("GOOGLE_WEB_CLIENT_JSON", json.dumps(WEB_CLIENT))
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(KYLE))  # Kyle's, on the server
    fam = family_mod.Family(config, client=fake_client)
    made = []
    zh_whisper, zh_voice = Whisper("你好"), Voice()

    def make_transcriber(model, language):
        made.append(("whisper", model, language))
        return zh_whisper

    def make_voice(path):
        made.append(("voice", path))
        return zh_voice

    speech = webapp.SpeechByLanguage("en", {"zh": ("small", "voices/zh.onnx")},
                                     make_transcriber, make_voice)
    en_whisper, en_voice = Whisper("hello"), Voice()
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        en_whisper, en_voice, tools=owner_registry(), clock=Clock(), family=fam,
        speech=speech)
    connect = google_web.GoogleConnect(config, assistant.tools, family=fam, owner=False)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, google=connect,
                            sessions_file=tmp_path / "sessions.json")

    class G:
        pass

    e = G()
    e.__dict__.update(config=config, fam=fam, assistant=assistant, connect=connect,
                      app=app, made=made, zh_whisper=zh_whisper, zh_voice=zh_voice,
                      en_whisper=en_whisper, en_voice=en_voice, client=fake_client,
                      tmp=tmp_path)

    def member(name="Mom", language="zh"):
        _, invite = fam.invite(name, language)
        c = TestClient(app, base_url=BASE)
        assert c.post("/api/invite/accept", json={"invite": invite}).status_code == 200
        uid = next(u["id"] for u in fam.members() if u["name"] == name)
        return c, uid

    e.member = member
    yield e
    fam.close()


def start(client) -> str:
    r = client.post("/api/google/connect", json={})
    assert r.status_code == 200, r.text
    assert client.cookies.get(google_web.BIND_COOKIE)  # this browser's binding
    return parse_qs(urlparse(r.json()["url"]).query)["state"][0]


def creds_for(who):
    creds = MagicMock(granted_scopes=None)
    creds.to_json.return_value = json.dumps(token_of(who))
    return creds


def callback(client, state, who="x", code="4/code"):
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as fetch, \
         patch("google_auth_oauthlib.flow.Flow.credentials", new=creds_for(who)):
        r = client.get("/google/callback", params={"state": state, "code": code},
                       follow_redirects=False)
    return r, fetch


def connected(g, client, uid, who):
    r, _ = callback(client, start(client), who)
    assert r.status_code == 303, r.text
    return json.loads(g.fam.google_token(uid).read_text())


def built_credentials(tool):
    """The credentials a tool's Google service is built with (the API client
    mocked); proves which token it really uses."""
    seen = {}

    def build(api, version, credentials=None, **kw):
        seen["creds"] = credentials
        return MagicMock()

    with patch("googleapiclient.discovery.build", side_effect=build):
        _ = tool.handler.__self__.service
    return seen["creds"]


# --- each person's own account -----------------------------------------------------

def test_each_member_has_their_own_token_never_kyles(g):
    mom, mom_id = g.member("Mom", "zh")
    dad, dad_id = g.member("Dad", "en")
    assert mom.get("/api/google/status").json() == {
        "enabled": True, "connected": False, "disconnect": True}
    assert connected(g, mom, mom_id, "mom")["refresh_token"] == "1//mom-refresh-secret"
    assert dad.get("/api/google/status").json()["connected"] is False  # not Mom's
    assert connected(g, dad, dad_id, "dad")["refresh_token"] == "1//dad-refresh-secret"
    # separate files in their own folders, private; Kyle's untouched
    assert g.fam.google_token(mom_id) != g.fam.google_token(dad_id)
    assert g.fam.google_token(mom_id).parent.name == mom_id
    import sys
    if sys.platform != "win32":
        assert g.fam.google_token(mom_id).stat().st_mode & 0o077 == 0
    assert not (g.tmp / "kyle_token.json").exists()
    for uid in (mom_id, dad_id):
        assert "kyle" not in g.fam.google_token(uid).read_text()
    # each one's tools really use their own token, though GOOGLE_TOKEN_JSON
    # (Kyle's) is set in this process
    for uid, who in ((mom_id, "mom"), (dad_id, "dad")):
        tools = g.fam.context(uid).registry.tools
        for name in ("list_calendar_events", "email_summary"):
            creds = built_credentials(tools[name])
            assert creds.refresh_token == f"1//{who}-refresh-secret", (uid, name)
    # and Kyle's shared tools got nothing of theirs
    assert set(g.assistant.tools.tools) == set(OWNER_TOOLS)


def test_the_owner_has_no_google_connect_on_the_cloud(g):
    """Kyle's Google on the server is GOOGLE_TOKEN_JSON: his session can't
    start this flow (owner=False), and nothing writes his token file."""
    kyle = TestClient(g.app, base_url=BASE)
    login(kyle)
    assert kyle.get("/api/google/status").status_code == 404
    assert kyle.post("/api/google/connect", json={}).status_code == 404
    assert kyle.post("/api/google/disconnect", json={}).status_code == 404
    assert "data-google" not in kyle.get("/").text
    mom, _ = g.member()
    assert "data-google" in mom.get("/").text  # her own Connect Google


def test_callback_with_another_sessions_state_is_refused(g):
    mom, mom_id = g.member("Mom")
    dad, dad_id = g.member("Dad", "en")
    moms_state = start(mom)
    start(dad)  # Dad has his own binding cookie, for his own state
    # Mom's consent link finished in Dad's browser: refused, nothing written
    r, fetch = callback(dad, moms_state, "dad")
    assert r.status_code == 400
    fetch.assert_not_called()
    assert not g.fam.google_token(mom_id).exists()
    assert not g.fam.google_token(dad_id).exists()
    # and it was used up: Mom's own browser can't finish it afterwards either
    r, fetch = callback(mom, moms_state, "mom")
    assert r.status_code == 400 and not fetch.called
    # a browser with no binding cookie at all
    state = start(mom)
    stranger = TestClient(g.app, base_url=BASE)
    r, fetch = callback(stranger, state, "eve")
    assert r.status_code == 400 and not fetch.called
    assert not g.fam.google_token(mom_id).exists()


def test_callback_after_signing_out_or_removal_is_refused(g):
    mom, mom_id = g.member("Mom")
    state = start(mom)
    assert mom.post("/api/logout").status_code == 200
    r, fetch = callback(mom, state, "mom")
    assert r.status_code == 400 and not fetch.called
    dad, dad_id = g.member("Dad", "en")
    state = start(dad)
    g.fam.remove(dad_id)
    r, fetch = callback(dad, state, "dad")
    assert r.status_code == 400 and not fetch.called
    assert not (g.tmp / "users" / dad_id).exists()


def test_failed_callback_page_is_in_their_language(g):
    mom, _ = g.member("Mom", "zh")
    state = start(mom)
    r = mom.get("/google/callback", params={"state": state, "error": "access_denied"},
                follow_redirects=False)
    assert r.status_code == 400 and r.text == i18n.ZH["google_failed"]


def test_tools_only_after_connecting(g):
    mom, mom_id = g.member("Mom", "en")
    g.client.queue.append(make_response(text_block("Hi.")))
    assert mom.post("/api/turn", json={"text": "hi"}).status_code == 200
    before = {t["name"] for t in g.client.calls[-1]["tools"]}
    assert before == {"web_search", "web_fetch"}
    session = next(s for s in g.assistant.sessions.values() if s.user == mom_id)
    assert not session.registry.has("list_calendar_events")
    connected(g, mom, mom_id, "mom")
    # her open conversation sees them at once (shared registry dict)
    assert session.registry.has("list_calendar_events")
    g.client.queue.append(make_response(text_block("Hi again.")))
    assert mom.post("/api/turn", json={"text": "hi"}).status_code == 200
    call = g.client.calls[-1]
    after = {t["name"] for t in call["tools"]}
    assert {"list_calendar_events", "create_calendar_event", "move_calendar_event",
            "delete_calendar_event", "email_summary", "read_email",
            "draft_reply"} <= after
    # nothing of Kyle's: no owner tool, and no "Kyle" in what Claude is told
    assert not after & set(OWNER_TOOLS)
    assert "Kyle" not in json.dumps(call["tools"]) and "Kyle" not in call["system"]
    assert "Mom's" in json.dumps(call["tools"])


def test_gmail_is_drafts_only_and_deletes_ask_for_family(g):
    from assistant.tools.gmail import SendingBlocked

    mom, mom_id = g.member("Mom")
    connected(g, mom, mom_id, "mom")
    tools = g.fam.context(mom_id).registry.tools
    assert not [n for n in tools if "send" in n]
    assert tools["draft_reply"].confirm_prompt is not None
    assert tools["delete_calendar_event"].confirm_prompt is not None
    assert tools["create_calendar_event"].confirm_if_untrusted is not None
    assert tools["move_calendar_event"].confirm_if_untrusted is not None
    for name in ("email_summary", "read_email"):
        assert tools[name].untrusted_output and tools[name].private
    inbox = tools["draft_reply"].handler.__self__
    with patch("googleapiclient.discovery.build", return_value=MagicMock()):
        service = inbox.service
    with pytest.raises(SendingBlocked):
        service.users().messages().send(userId="me", body={})
    with pytest.raises(SendingBlocked):
        service.users().drafts().send(userId="me", body={})
    # never an interactive sign-in on the server, never the environment's token
    assert inbox.save_token is False and inbox.environ == {}


def test_disconnect_removes_the_token_and_the_tools(g):
    mom, mom_id = g.member("Mom")
    connected(g, mom, mom_id, "mom")
    session = g.assistant.session(next(s for s in g.app.state.web.store.users
                                       if g.app.state.web.store.users[s] == mom_id))
    assert session.registry.has("email_summary")
    r = mom.post("/api/google/disconnect", json={})
    assert r.status_code == 200
    assert not g.fam.google_token(mom_id).exists()
    assert not session.registry.has("email_summary")
    assert not session.registry.has("list_calendar_events")
    assert session.registry.has("web_search")
    assert mom.get("/api/google/status").json()["connected"] is False


def test_removing_a_member_deletes_their_token(g):
    mom, mom_id = g.member("Mom")
    dad, dad_id = g.member("Dad", "en")
    connected(g, mom, mom_id, "mom")
    connected(g, dad, dad_id, "dad")
    kyle = TestClient(g.app, base_url=BASE)
    login(kyle)
    assert kyle.post(f"/api/users/{mom_id}/remove", json={}).status_code == 200
    assert not g.fam.google_token(mom_id).exists()
    assert not (g.tmp / "users" / mom_id).exists()
    assert g.fam.google_token(dad_id).exists()  # Dad's stays
    assert g.fam.context(mom_id) is None


def test_no_secrets_or_names_at_info(g, caplog):
    caplog.set_level(logging.DEBUG)
    mom, mom_id = g.member("Mom")
    state = start(mom)
    r, _ = callback(mom, state, "mom", code="4/secret-code")
    assert r.status_code == 303
    mom.post("/api/google/disconnect", json={})
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("assistant")]
    assert any(mom_id in m and "connected their Google" in m for m in info)
    for secret in ("4/secret-code", state, "ya29", "refresh-secret", "Mom",
                   mom.cookies.get(google_web.BIND_COOKIE) or "no-cookie"):
        assert not any(secret in m for m in info), secret


def test_google_off_without_a_web_client(g, monkeypatch):
    monkeypatch.delenv("GOOGLE_WEB_CLIENT_JSON")
    mom, _ = g.member("Mom")
    assert mom.get("/api/google/status").json()["enabled"] is False
    assert mom.post("/api/google/connect", json={}).status_code == 503
    assert "data-google" not in mom.get("/").text
    # a Desktop ("installed") client can't take the web redirect: off too
    monkeypatch.setenv("GOOGLE_WEB_CLIENT_JSON", json.dumps({"installed": WEB_CLIENT["web"]}))
    assert mom.get("/api/google/status").json()["enabled"] is False


def test_from_config_builds_it_for_family_only_servers(g):
    cfg = load_config(None, None, profile="server")
    a = MagicMock(family=g.fam)
    connect = google_web.from_config(cfg, a)
    assert connect is not None and connect.owner is False  # the cloud: Kyle's is env
    cfg["family"]["google"] = False
    assert google_web.from_config(cfg, a) is None
    assert google_web.from_config(cfg, MagicMock(family=None)) is None


# --- speech in each person's language ----------------------------------------------

def test_zh_session_uses_chinese_speech_and_english_does_not(g):
    mom, _ = g.member("Mom", "zh")
    dad, _ = g.member("Dad", "en")
    g.client.queue.append(make_response(text_block("你好！")))
    lines = mom.post("/api/turn", files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
                     ).text.splitlines()
    events = [json.loads(x) for x in lines]
    assert events[0] == {"type": "transcript", "text": "你好"}
    assert g.zh_whisper.heard == 1 and g.en_whisper.heard == 0
    reply = next(e for e in events if e["type"] == "reply")
    assert mom.get(reply["audio"]).status_code == 200
    assert g.zh_voice.texts == ["你好！"] and g.en_voice.texts == []
    g.client.queue.append(make_response(text_block("Hello!")))
    lines = dad.post("/api/turn", files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
                     ).text.splitlines()
    events = [json.loads(x) for x in lines]
    assert events[0] == {"type": "transcript", "text": "hello"}
    assert g.en_whisper.heard == 1 and g.zh_whisper.heard == 1
    reply = next(e for e in events if e["type"] == "reply")
    assert dad.get(reply["audio"]).status_code == 200
    assert g.en_voice.texts == ["Hello!"] and g.zh_voice.texts == ["你好！"]
    # a second Chinese speaker shares the same models (cached per model)
    aunt, _ = g.member("Aunt", "zh")
    g.client.queue.append(make_response(text_block("好")))
    aunt.post("/api/turn", files={"audio": ("a.wav", wav_bytes(), "audio/wav")})
    assert g.zh_whisper.heard == 2
    assert g.made == [("whisper", "small", "zh"), ("voice", "voices/zh.onnx")]


def test_zh_voice_conversation_uses_chinese_speech(g):
    from tests.test_voice_ws import connect as ws_connect, receive, utterance

    mom, _ = g.member("Mom", "zh")
    g.client.queue.append(make_response(text_block("你好，有什么可以帮你？")))
    with ws_connect(mom) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_bytes(utterance(1))
        msgs = receive(ws, "turn_done")
        ws.send_text(json.dumps({"type": "end"}))
    assert msgs[0]["type"] == "transcript" and msgs[0]["text"] == "你好"
    assert g.zh_whisper.heard == 1 and g.en_whisper.heard == 0
    assert g.zh_voice.texts and not g.en_voice.texts


def test_speech_models_are_lazy_and_english_servers_keep_theirs():
    made = []
    speech = webapp.SpeechByLanguage(
        "en", {"zh": ("small", "zh.onnx")},
        lambda m, lang: made.append(m) or object(), lambda v: made.append(v) or object())
    assert made == []  # nothing loaded until someone speaks Chinese
    assert speech.transcriber("en") is None and speech.tts("en") is None
    assert speech.transcriber(None) is None
    first = speech.transcriber("zh-CN")
    assert speech.transcriber("zh") is first and made == ["small"]
    # a Chinese server: its own models are already Chinese
    zh_server = webapp.SpeechByLanguage("zh", {"zh": ("small", "zh.onnx")},
                                        lambda *a: 1 / 0, lambda *a: 1 / 0)
    assert zh_server.transcriber("zh") is None and zh_server.tts("zh") is None


def test_family_speech_from_config():
    config = load_config(None, None)
    speech = webapp.family_speech(config)
    t = speech.transcriber("zh")
    assert (t.model_name, t.language) == (config["family"]["zh_stt_model"], "zh")
    assert speech.transcriber("en") is None


# --- server sentences in each person's language --------------------------------------

def test_i18n_t_follows_the_session_language():
    assert i18n.t("goodbye") == "Goodbye!"
    assert i18n.t("goodbye", "zh") == "再见！" and i18n.t("goodbye", "zh-CN") == "再见！"
    with i18n.using("zh"):
        assert i18n.t("goodbye") == "再见！" and i18n.is_zh()
        seen = []
        other = threading.Thread(target=lambda: seen.append(i18n.t("goodbye")))
        other.start()
        other.join()
        assert seen == ["Goodbye!"]  # another thread (another turn) isn't affected
        with i18n.using(None):
            assert i18n.t("goodbye") == "Goodbye!"
    assert i18n.t("goodbye") == "Goodbye!" and not i18n.is_zh()
    i18n.set_language("zh")
    try:
        with i18n.using("en"):  # an English member on a Chinese server
            assert i18n.t("goodbye") == "Goodbye!"
    finally:
        i18n.set_language("en")


def test_push_to_talk_sentences_are_in_each_persons_language(g):
    mom, _ = g.member("Mom", "zh")
    dad, _ = g.member("Dad", "en")
    g.zh_whisper.text = g.en_whisper.text = ""  # nothing heard
    for client, expected in ((mom, i18n.ZH["not_caught"]), (dad, i18n.EN["not_caught"])):
        events = [json.loads(x) for x in client.post(
            "/api/turn", files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
        ).text.splitlines()]
        assert {"type": "error", "message": expected} in events


def test_a_calendar_question_is_asked_in_her_language(g):
    """The tool's confirmation question is built in the turn's thread: with
    the server in English, Mom's is still Chinese."""
    mom, mom_id = g.member("Mom", "zh")
    connected(g, mom, mom_id, "mom")
    g.client.queue.append(make_response(
        tool_use_block("delete_calendar_event", {"event_id": "e1"}), stop_reason="tool_use"))
    with patch("googleapiclient.discovery.build", side_effect=RuntimeError("offline")):
        lines = mom.post("/api/turn", json={"text": "删掉明天的会"}).text.splitlines()
    confirm = next(json.loads(x) for x in lines if '"confirm"' in x)
    assert confirm["question"] == i18n.ZH["cal_delete_any"]
    g.client.queue.append(make_response(text_block("好的，不删了。")))
    r = mom.post("/api/confirm", json={"answer": "不要"})
    assert r.status_code == 200


def test_voice_conversation_says_its_own_sentences_in_its_language():
    from assistant.voice_session import decline_message, question_suffix

    assert question_suffix("zh") == i18n.ZH["say_yes_no"]
    assert question_suffix() == i18n.EN["say_yes_no"]
    assert decline_message("要我删除这个日程吗？", "zh") == i18n.ZH["wont_delete"]
    assert decline_message("Add it?", "en") == i18n.EN["wont_do"]


def test_the_web_app_gets_family_speech_only_with_family_on(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = load_config(None, None)
    cfg["calendar"]["enabled"] = cfg["history"]["enabled"] = False
    assert webapp.build_assistant(cfg, "print", output_fn=lambda m: None).speech is None
    cfg["family"].update(enabled=True, dir=str(tmp_path / "users"))
    a = webapp.build_assistant(cfg, "print", output_fn=lambda m: None)
    try:
        assert a.speech.models == {"zh": (cfg["family"]["zh_stt_model"],
                                          cfg["family"]["zh_piper_voice"])}
        assert a.speech._transcribers == {} and a.speech._voices == {}  # lazy
    finally:
        a.shutdown()


def test_busy_voice_turn_says_so_in_her_language():
    from types import SimpleNamespace

    from assistant.voice_session import WebBackend

    def busy(*a, **k):
        raise RuntimeError("a turn is already running in this session")
        yield  # a generator, like chat_stream

    said = []
    backend = WebBackend(SimpleNamespace(chat_stream=busy), SimpleNamespace(language="zh"))
    assert backend.ask("你好", said.append, lambda n: None, None) == i18n.ZH["still_working"]


def test_a_family_token_without_client_fields_never_reads_a_client_file(tmp_path,
                                                                        monkeypatch):
    """Family tools pass credentials_path="" (their token carries its client):
    "" must mean no file, not the current folder."""
    from assistant.tools import google_auth

    monkeypatch.chdir(tmp_path)
    assert google_auth.client_config("", environ={}) is None
    token = tmp_path / "t.json"
    token.write_text(json.dumps({"token": "x", "refresh_token": "r"}))
    info, from_env = google_auth.token_info(token, "", environ={})
    assert info["token"] == "x" and from_env is False


# --- round 2 (judge) ----------------------------------------------------------------

def _refused(self, request):
    from google.auth.exceptions import RefreshError

    raise RefreshError("invalid_grant: Token has been expired or revoked.")


def test_an_expired_family_token_says_reconnect_in_the_app(g, monkeypatch, caplog):
    """Never Kyle's advice (his PC sign-in, token.json, GOOGLE_TOKEN_JSON):
    the tool error tells her to reconnect in the app, in her language."""
    from google.oauth2.credentials import Credentials

    mom, mom_id = g.member("Mom", "zh")
    connected(g, mom, mom_id, "mom")
    stale = {k: v for k, v in token_of("mom").items() if k != "expiry"}
    g.fam.google_token(mom_id).write_text(json.dumps(stale))  # needs a refresh
    monkeypatch.setattr(Credentials, "refresh", _refused)
    caplog.set_level(logging.WARNING)
    registry = g.fam.context(mom_id).registry
    for lang, hint in (("en", i18n.EN["google_reconnect"]),
                       ("zh", i18n.ZH["google_reconnect"])):
        for tool, args in (("list_calendar_events", {"day": "today"}),
                           ("email_summary", {})):  # Calendar and Gmail alike
            registry.tools[tool].handler.__self__._service = None
            with i18n.using(lang):
                result, is_error = registry.dispatch(tool, args)
            assert is_error, tool
            assert hint in result, (tool, result)
            assert "Kyle" not in result and "GOOGLE_TOKEN_JSON" not in result
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("reconnect Google in the app" in m for m in warnings)
    assert not any("GOOGLE_TOKEN_JSON" in m or "token.json" in m for m in warnings)


def test_family_scope_and_missing_token_errors_say_reconnect_in_the_app(tmp_path):
    from assistant.tools import google_auth

    token = tmp_path / "t.json"
    token.write_text(json.dumps({**token_of("mom"), "scopes": SCOPES[:1]}))  # calendar only
    with pytest.raises(google_auth.MissingScopes) as e:
        google_auth.get_credentials("", token, save_token=False, environ={},
                                    scopes=google_auth.GMAIL_SCOPES, in_app=True)
    assert i18n.EN["google_reconnect"] in str(e.value)
    assert google_auth.SIGN_IN_COMMAND not in str(e.value)
    with pytest.raises(google_auth.GoogleSignInError) as e:  # disconnected meanwhile
        google_auth.get_credentials("", tmp_path / "gone.json", save_token=False,
                                    environ={}, in_app=True)
    assert i18n.EN["google_reconnect"] in str(e.value)
    assert "GOOGLE_TOKEN_JSON" not in str(e.value)
    # Kyle's own errors are unchanged
    with pytest.raises(google_auth.MissingScopes) as e:
        google_auth.get_credentials("", token, save_token=False, environ={},
                                    scopes=google_auth.GMAIL_SCOPES)
    assert google_auth.SIGN_IN_COMMAND in str(e.value)


def test_removed_while_google_answers_writes_no_token(g):
    """Removed after the callback's check but before the token is saved:
    save_google re-checks under the lock and writes nothing."""
    mom, mom_id = g.member("Mom")
    state = start(mom)

    def remove_meanwhile(*a, **k):
        g.fam.remove(mom_id)

    with patch("google_auth_oauthlib.flow.Flow.fetch_token", side_effect=remove_meanwhile), \
         patch("google_auth_oauthlib.flow.Flow.credentials", new=creds_for("mom")):
        r = mom.get("/google/callback", params={"state": state, "code": "c"},
                    follow_redirects=False)
    assert r.status_code == 400
    assert not g.fam.google_token(mom_id).exists()
    assert not (g.tmp / "users" / mom_id).exists()


def test_binding_cookie_attributes(g):
    mom, _ = g.member("Mom")
    r = mom.post("/api/google/connect", json={})
    cookie = next(v for k, v in r.headers.multi_items()
                  if k == "set-cookie" and v.startswith(google_web.BIND_COOKIE + "="))
    attrs = {a.strip().split("=", 1)[0].lower(): (a.strip().split("=", 1) + [""])[1]
             for a in cookie.split(";")[1:]}
    assert "httponly" in attrs and "secure" in attrs
    assert attrs["samesite"].lower() == "lax"
    assert attrs["path"] == "/google/callback"
    assert attrs["max-age"] == "600"


def test_a_calendar_question_by_voice_is_asked_in_her_language(g):
    """The voice twin of the push-to-talk test: chat_stream's thread runs in
    the session's language, so the question over the socket is Chinese."""
    from tests.test_voice_ws import connect as ws_connect, receive, utterance

    mom, mom_id = g.member("Mom", "zh")
    connected(g, mom, mom_id, "mom")
    # "你好" goes to the fast model; its delete call is dropped (router.py)
    # and the main model's own delete call asks the question
    for _ in range(2):
        g.client.queue.append(make_response(
            tool_use_block("delete_calendar_event", {"event_id": "e1"}),
            stop_reason="tool_use"))
    g.client.queue.append(make_response(text_block("好的，不删了。")))
    with patch("googleapiclient.discovery.build", side_effect=RuntimeError("offline")):
        with ws_connect(mom) as ws:
            assert json.loads(ws.receive_text())["type"] == "ready"
            ws.send_bytes(utterance(1))
            confirm = receive(ws, "confirm")[-1]
            ws.send_text(json.dumps({"type": "end"}))
    assert confirm["question"] == i18n.ZH["cal_delete_any"]


def test_voice_worker_runs_in_the_conversations_language():
    """VoiceConversation._work's `using`: a backend that answers in the
    worker thread (ChatBackend) asks its tool's question in Chinese."""
    from assistant.tools import Tool, ToolRegistry
    from tests.test_voice import SPEECH, make_conv
    from tests.voice_fakes import stream_client, streamed

    reg = ToolRegistry()
    reg.register(Tool(name="delete_event", description="Delete.",
                      input_schema={"type": "object", "properties": {}},
                      handler=lambda: {"deleted": True},
                      confirm_prompt=lambda i: i18n.t("cal_delete_any")))
    client = stream_client(
        streamed("好。", extra=[tool_use_block("delete_event", {})], stop_reason="tool_use"),
        streamed("好的。"))
    conv, _, events = make_conv(client, "删掉", "不要", tools=reg, language="zh")
    conv.utterance(1, SPEECH)
    assert events.wait_for("confirm")["question"] == i18n.ZH["cal_delete_any"]
    asked = events.wait_for("audio", question=True)
    assert asked["text"].endswith(i18n.ZH["say_yes_no"])
    conv.end("user")


def test_owner_of_stays_for_owner_only_routes(g):
    """create_app keeps owner_of (other branches' owner-only routes use it):
    the owner gets his session, a family member 404, nobody 401."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    def owner_thing(request: Request):
        s = request.app.state.owner_of(request)
        return {"ok": True} if s is not None else JSONResponse({}, 401)

    g.app.add_api_route("/api/owner-thing", owner_thing, methods=["GET"])
    kyle = TestClient(g.app, base_url=BASE)
    assert kyle.get("/api/owner-thing").status_code == 401
    login(kyle)
    assert kyle.get("/api/owner-thing").json() == {"ok": True}
    mom, _ = g.member()
    assert mom.get("/api/owner-thing").status_code == 404


def test_a_picked_voice_wins_over_the_language_voice():
    """voice_picker (each person's pick) and SpeechByLanguage (a Chinese
    member's Mandarin voice) together: a pick wins; without one, her
    language's voice; the owner keeps the server's."""
    from types import SimpleNamespace

    from assistant.webapp import WebAssistant

    default, mandarin, picked = object(), object(), object()
    wa = object.__new__(WebAssistant)
    wa.tts = default
    wa.speech = SimpleNamespace(tts=lambda lang: mandarin if lang == "zh" else None)
    choices = {}
    wa.voices = SimpleNamespace(
        engine_for=lambda user, lang: choices.get(user, default))
    mom = SimpleNamespace(user="mom", language="zh")
    kyle = SimpleNamespace(user=None, language="en")
    assert wa.tts_for(mom) is mandarin
    assert wa.tts_for(kyle) is default
    choices["mom"] = picked
    assert wa.tts_for(mom) is picked
    wa.voices = None
    assert wa.tts_for(mom) is mandarin
