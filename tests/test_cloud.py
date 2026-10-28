"""The cloud server (ASSISTANT_PROFILE=server, --mode web on Render).

CloudGuard in front of the web app: /healthz, the auth wall, login lockout
per address, forwarded headers trusted only from the proxy, body limits,
headers and errors. Plus the headless server profile and --check variant.
Everything is offline: the web app runs on the mocked Anthropic client.
"""

import json
import logging
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from kwil import cloud, doctor, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import DEFAULTS, load_config  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402
from tests.test_webapp import TOKEN, Clock, FakeTranscriber, wav_bytes  # noqa: E402

PROXY = "10.1.2.3"        # Render's TLS proxy reaches the container privately
PUBLIC = "203.0.113.9"    # someone on the internet connecting directly
TRUSTED = ["10.0.0.0/8", "127.0.0.0/8"]  # Render-like proxy + this machine


def at(peer, app):
    """The app as seen from `peer` (the TCP address the server reports)."""
    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (peer, 40000)}
        await app(scope, receive, send)
    return wrapped


@pytest.fixture
def slept():
    """Delays CloudGuard held wrong tokens for (recorded, not waited)."""
    return []


@pytest.fixture
def make_cloud(fake_client, slept):
    def make(peer=PROXY, clock=None, guard_kwargs=None, app=None):
        clock = clock or Clock()
        limiter = (guard_kwargs or {}).get("lockout") or cloud.BoundedLoginLimiter(clock=clock)
        if app is None:
            assistant = webapp.WebAssistant(
                lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
                FakeTranscriber("what's on my calendar"), PrintTTS(), clock=clock)
            trust = cloud.ProxyNetworks(hops=1, networks=tuple(
                cloud.parse_networks(TRUSTED)))
            app = webapp.create_app(assistant, TOKEN, limiter=limiter,
                                    trusted_proxy=trust,
                                    reap_interval=3600)
        async def sleep(seconds):
            slept.append(seconds)

        kwargs = {"sleep": sleep, "trusted_proxies": TRUSTED, "max_upload_bytes": 200_000,
                  "max_body_bytes": 16_384,
                  "lockout": limiter,
                  "limiter": cloud.RateLimiter(clock=clock),
                  **(guard_kwargs or {})}
        guard = cloud.CloudGuard(app, TOKEN, **kwargs)
        client = TestClient(at(peer, guard), base_url="https://testserver",
                            follow_redirects=False)
        return guard, client
    return make


def via_proxy(ip, proto="https", **extra):
    return {"X-Forwarded-For": ip, "X-Forwarded-Proto": proto, **extra}


def login(client, token=TOKEN, ip=PUBLIC):
    return client.post("/api/login", json={"token": token}, headers=via_proxy(ip))


# --- /healthz -----------------------------------------------------------------

def test_healthz_is_unauthenticated_and_reveals_nothing(make_cloud):
    _, client = make_cloud()
    r = client.get("/healthz")
    assert r.status_code == 200 and set(r.json()) == {"ok", "version"}  # no "busy"
    assert r.json()["ok"] is True
    assert "server" not in r.headers and "set-cookie" not in r.headers
    assert r.headers["cache-control"] == "no-store"
    # health checks come over plain http from inside Render: no redirect
    assert client.get("/healthz", headers=via_proxy(PUBLIC, "http")).status_code == 200
    assert client.post("/healthz").status_code == 401  # only GET/HEAD


# --- the auth wall ------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/app.js", "/app.css", "/sw.js",
                                  "/manifest.webmanifest", "/icon.svg",
                                  "/icon-192.png", "/icon-512.png", "/icon-180.png",
                                  "/icon-maskable.png"])
def test_login_page_manifest_and_icons_are_public(make_cloud, path):
    _, client = make_cloud()
    assert client.get(path, headers=via_proxy(PUBLIC)).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/state"), ("GET", "/api/events"), ("GET", "/api/audio/1-abc"),
    ("POST", "/api/turn"), ("POST", "/api/confirm"), ("POST", "/api/logout"),
    ("GET", "/silence.wav"), ("GET", "/docs"), ("GET", "/openapi.json"),
    ("GET", "/web/"), ("GET", "/assistant/"), ("GET", "/.env"),
    ("GET", "/../config.toml"), ("GET", "/web/index.html"), ("GET", "/nope"),
    ("PUT", "/"), ("DELETE", "/api/state")])
def test_everything_else_needs_the_session_cookie(make_cloud, method, path):
    _, client = make_cloud()
    r = client.request(method, path, headers=via_proxy(PUBLIC))
    assert r.status_code == 401
    assert r.json() == {"error": "not signed in"}
    assert "Content-Security-Policy" in r.headers


def test_a_forged_or_other_token_cookie_is_refused(make_cloud):
    _, client = make_cloud()
    forged = webapp.make_cookie("x" * 40, "sid")
    r = client.get("/api/state", headers={**via_proxy(PUBLIC),
                                         "Cookie": f"{webapp.COOKIE}={forged}"})
    assert r.status_code == 401


def test_signed_in_routes_work_through_the_guard(make_cloud, fake_client):
    _, client = make_cloud()
    r = login(client)
    assert r.status_code == 200
    assert client.get("/api/state", headers=via_proxy(PUBLIC)).status_code == 200
    assert client.get("/silence.wav", headers=via_proxy(PUBLIC)).status_code == 200
    fake_client.queue.append(make_response(text_block("You have nothing tomorrow.")))
    r = client.post("/api/turn", json={"text": "tomorrow?"}, headers=via_proxy(PUBLIC))
    events = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in events][-2:] == ["reply", "done"]
    audio_url = events[-2]["audio"]
    assert client.get(audio_url, headers=via_proxy(PUBLIC)).status_code == 200


def test_bearer_token_works_and_wrong_ones_count_toward_the_lockout(make_cloud):
    _, client = make_cloud()
    ok = {**via_proxy(PUBLIC), "Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/state", headers=ok).status_code == 200
    bad = {**via_proxy(PUBLIC), "Authorization": "Bearer " + "x" * 43}
    for _ in range(5):
        assert client.get("/api/state", headers=bad).status_code == 401
    assert client.get("/api/state", headers=bad).status_code == 429  # locked
    assert login(client, "wrong-token").status_code == 429  # one lockout for both
    # the right token is never locked out
    assert client.get("/api/state", headers=ok).status_code == 200
    assert login(client).status_code == 200


def test_chunked_upload_under_the_cap_reaches_the_app(make_cloud, fake_client):
    _, client = make_cloud()
    login(client)
    fake_client.queue.append(make_response(text_block("Hi.")))
    body = json.dumps({"text": "hello"}).encode()
    r = client.post("/api/turn", content=iter([body[:5], body[5:]]), headers={
        **via_proxy(PUBLIC), "Content-Type": "application/json"})
    assert r.status_code == 200 and "Hi." in r.text


def test_session_cookie_is_secure_httponly_strict(make_cloud):
    _, client = make_cloud()
    cookie = login(client).headers["set-cookie"].lower()
    for part in ("secure", "httponly", "samesite=strict", "path=/"):
        assert part in cookie


# --- login lockout ------------------------------------------------------------

def test_wrong_tokens_lock_out_that_address_only(make_cloud, slept):
    clock = Clock()
    _, client = make_cloud(clock=clock)
    for _ in range(5):
        assert login(client, "wrong" * 10).status_code == 401
    r = login(client, "wrong" * 10)
    assert r.status_code == 429 and int(r.headers["retry-after"]) == 900
    assert slept == []  # refused at once, not counted again
    assert login(client, "x", ip="198.51.100.7").status_code == 401  # others: as usual
    clock.now += 901
    assert login(client, "wrong" * 10).status_code == 401  # lockout over


def test_the_right_token_is_never_locked_out(make_cloud):
    """Otherwise anyone could keep Kyle from signing in."""
    _, client = make_cloud()
    for _ in range(12):
        login(client, "bad-token")
    assert login(client).status_code == 200


def test_signed_in_phone_keeps_working_during_a_lockout(make_cloud):
    _, client = make_cloud()
    assert login(client).status_code == 200
    for _ in range(6):
        client.post("/api/login", json={"token": "nope"}, headers=via_proxy(PUBLIC))
    assert client.get("/api/state", headers=via_proxy(PUBLIC)).status_code == 200


def test_global_brake_slows_wrong_tokens_only(make_cloud, slept):
    _, client = make_cloud(guard_kwargs={"lockout": cloud.BoundedLoginLimiter(
        per_ip=100, global_free=10, clock=Clock())})
    for i in range(10):
        assert login(client, "bad", ip=f"198.51.100.{i}").status_code == 401
    assert slept == []
    assert login(client, "bad", ip="192.0.2.1").status_code == 401
    assert slept == [1.0]  # past 10, wrong tokens from anyone wait a second
    assert login(client, ip="192.0.2.2").status_code == 200  # the right one doesn't
    assert slept == [1.0]


def test_guard_and_web_app_share_one_limiter_and_session_store(fake_client):
    cfg = load_config(None, None, profile="server")
    cfg["webapp"]["sessions_file"] = ""
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS())
    guard = cloud.build_app(cfg, assistant, TOKEN)
    assert guard.web is guard.app.state.web
    assert guard.lockout is guard.web.limiter
    assert isinstance(guard.lockout, webapp.LoginLimiter)


def test_signing_out_revokes_the_cookie_at_the_guard_too(make_cloud):
    _, client = make_cloud()
    login(client)
    cookie = client.cookies.get(webapp.COOKIE)
    assert client.post("/api/logout", headers=via_proxy(PUBLIC)).status_code == 200
    r = client.get("/api/state", headers={**via_proxy(PUBLIC),
                                         "Cookie": f"{webapp.COOKIE}={cookie}"})
    assert r.status_code == 401


def test_spoofed_forwarded_for_does_not_dodge_the_lockout(make_cloud):
    # the client adds its own X-Forwarded-For; the proxy appends the real one
    _, client = make_cloud()
    for i in range(5):
        client.post("/api/login", json={"token": "bad"},
                    headers=via_proxy(f"192.0.2.{i}, {PUBLIC}"))
    assert client.post("/api/login", json={"token": "bad"},
                       headers=via_proxy(f"192.0.2.99, {PUBLIC}")).status_code == 429


def test_forwarded_headers_are_ignored_from_a_direct_connection(make_cloud):
    guard, client = make_cloud(peer=PUBLIC)
    for i in range(5):
        client.post("/api/login", json={"token": "bad"},
                    headers=via_proxy(f"192.0.2.{i}"))
    assert guard.lockout.retry_after(PUBLIC) > 0  # counted against the peer
    # and X-Forwarded-Proto: http from it causes no redirect
    assert client.get("/", headers=via_proxy("1.2.3.4", "http")).status_code == 200


# --- https behind the proxy ---------------------------------------------------

def test_plain_http_via_the_proxy_is_redirected(make_cloud):
    _, client = make_cloud()
    r = client.get("/api/state?x=1", headers=via_proxy(PUBLIC, "http",
                                                       Host="hey.onrender.com"))
    assert r.status_code == 308
    assert r.headers["location"] == "https://hey.onrender.com/api/state?x=1"


def test_hsts_only_over_https(make_cloud):
    _, client = make_cloud()
    assert "max-age" in client.get("/", headers=via_proxy(PUBLIC)).headers[
        "strict-transport-security"]
    _, direct = make_cloud(peer=PUBLIC)
    assert "strict-transport-security" not in direct.get("/").headers


# --- headers, CORS, errors ----------------------------------------------------

def test_security_headers_and_no_cors(make_cloud):
    _, client = make_cloud()
    r = client.options("/api/turn", headers={
        **via_proxy(PUBLIC), "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST"})
    assert r.status_code == 401
    assert not any(h.startswith("access-control-") for h in r.headers)
    r = client.get("/", headers=via_proxy(PUBLIC))
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert not any(h.startswith("access-control-") for h in r.headers)


def test_cross_site_requests_are_refused_even_when_signed_in(make_cloud):
    _, client = make_cloud()
    login(client)
    r = client.post("/api/turn", json={"text": "hi"}, headers={
        **via_proxy(PUBLIC), "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_errors_never_show_a_stack_trace(make_cloud, caplog):
    async def broken(scope, receive, send):
        raise RuntimeError("secret internals at /app/kwil/x.py line 3")

    _, client = make_cloud(app=broken)
    with caplog.at_level(logging.ERROR):
        r = client.get("/", headers=via_proxy(PUBLIC))
    assert r.status_code == 500 and r.json() == {"error": "internal error"}
    assert "Traceback" not in r.text and "internals" not in r.text
    assert "secret internals" in caplog.text  # but it is in the log


# --- request size limits ------------------------------------------------------

def test_oversized_bodies_are_refused(make_cloud):
    _, client = make_cloud()
    big = {"token": "x" * 20_000}
    assert client.post("/api/login", json=big,
                       headers=via_proxy(PUBLIC)).status_code == 413
    login(client)
    r = client.post("/api/turn", files={"audio": ("a.wav", b"\0" * 250_000)},
                    headers=via_proxy(PUBLIC))
    assert r.status_code == 413


def test_chunked_uploads_without_length_are_capped(make_cloud):
    _, client = make_cloud()
    login(client)

    def chunks():
        for _ in range(30):
            yield b"\0" * 10_000

    r = client.post("/api/turn", content=chunks(), headers={
        **via_proxy(PUBLIC), "Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413


def test_audio_under_the_limit_goes_through(make_cloud, fake_client):
    _, client = make_cloud()
    login(client)
    fake_client.queue.append(make_response(text_block("Nothing tomorrow.")))
    r = client.post("/api/turn", files={"audio": ("a.wav", wav_bytes(1.0))},
                    headers=via_proxy(PUBLIC))
    assert r.status_code == 200
    assert '"transcript"' in r.text and "Nothing tomorrow." in r.text


# --- rate limit ---------------------------------------------------------------

def test_requests_per_minute_per_address(make_cloud):
    clock = Clock()
    _, client = make_cloud(clock=clock, guard_kwargs={
        "limiter": cloud.RateLimiter(per_minute=3, clock=clock)})
    for _ in range(3):
        assert client.get("/", headers=via_proxy(PUBLIC)).status_code == 200
    assert client.get("/", headers=via_proxy(PUBLIC)).status_code == 429
    assert client.get("/", headers=via_proxy("198.51.100.1")).status_code == 200
    assert client.get("/healthz").status_code == 200  # never limited
    clock.now += 61
    assert client.get("/", headers=via_proxy(PUBLIC)).status_code == 200


# --- startup ------------------------------------------------------------------

def test_cloud_needs_a_web_token_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError) as e:
        cloud.cloud_token({})
    assert "WEB_TOKEN" in str(e.value)
    with pytest.raises(ValueError):
        cloud.cloud_token({"WEB_TOKEN": "short"})
    assert cloud.cloud_token({"WEB_TOKEN": TOKEN}) == TOKEN
    assert not (tmp_path / ".env").exists()  # never generated or saved


def test_build_app_uses_the_cloud_settings(make_cloud, fake_client):
    cfg = load_config(None, None, profile="server")
    assert cfg["webapp"]["sessions_file"] == "/data/web_sessions.json"  # the disk
    cfg["webapp"]["sessions_file"] = ""
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS())
    guard = cloud.build_app(cfg, assistant, TOKEN)
    assert isinstance(guard, cloud.CloudGuard)
    assert guard.max_upload_bytes == 4_000_000 and guard.max_body_bytes == 16_384
    assert guard.lockout.per_ip == 5 and guard.limiter.per_minute == 120
    client = TestClient(at(PROXY, guard), base_url="https://testserver")
    assert client.get("/healthz").json()["ok"] is True


def test_cli_web_mode_on_a_server_runs_the_cloud_app(monkeypatch, tmp_path,
                                                     restore_root_logging):
    from kwil import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSISTANT_PROFILE", "server")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(cloud, "serve", lambda config, **kw: calls.append(kw) or 0)
    assert cli.main([]) == 0  # web is the default mode on a server
    assert calls and calls[0]["port"] is None
    assert cli.main(["--mode", "wake"]) == 1  # no microphone on a server
    assert not (tmp_path / "logs").exists()  # logs go to stdout


def test_web_mode_never_imports_sounddevice_or_the_wake_word():
    code = ("import sys; sys.modules['sounddevice'] = None\n"
            "import os; os.environ['ANTHROPIC_API_KEY'] = 'x'\n"
            "from kwil import cli, cloud, webapp\n"
            "from kwil.config import load_config\n"
            "cfg = load_config(None, None, profile='server')\n"
            "cfg['tts']['engine'] = 'print'\n"
            "a = webapp.build_assistant(cfg, None, lambda m: None)\n"
            "cloud.build_app(cfg, a, 'x' * 40)\n"
            "bad = [m for m in ('openwakeword', 'pyttsx3') if m in sys.modules]\n"
            "assert not bad, bad\n"
            "print('ok')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")


# --- the voice WebSocket (/api/voice) -----------------------------------------

ORIGIN = "https://testserver"


def voice(client, ip=PUBLIC, origin=ORIGIN, cookie=True, path="/api/voice", **extra):
    headers = {"X-Forwarded-For": ip, "X-Forwarded-Proto": "https", **extra}
    if origin:
        headers["Origin"] = origin
    value = client.cookies.get(webapp.COOKIE)
    if cookie and value:  # TestClient speaks ws://, which drops Secure cookies
        headers["Cookie"] = f"{webapp.COOKIE}={value}"
    return client.websocket_connect(path, headers=headers)


def refused(ctx) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        with ctx:
            pass
    return e.value.code


def test_voice_socket_opens_when_signed_in_from_this_site(make_cloud):
    _, client = make_cloud()
    login(client)
    with voice(client):
        pass


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null",
                                    "https://testserver.evil.example"])
def test_voice_socket_needs_this_sites_origin(make_cloud, origin):
    _, client = make_cloud()
    login(client)
    assert refused(voice(client, origin=origin)) == 1008


def test_voice_socket_needs_auth(make_cloud):
    _, client = make_cloud()
    assert refused(voice(client)) == 1008
    login(client)
    assert refused(voice(client, cookie=False)) == 1008
    assert refused(voice(client, path="/api/other")) == 1008


def test_voice_socket_bearer_and_lockout(make_cloud):
    _, client = make_cloud()
    with voice(client, cookie=False, Authorization=f"Bearer {TOKEN}"):
        pass
    for _ in range(5):
        assert refused(voice(client, cookie=False, Authorization="Bearer wrong")) == 1008
    assert login(client, "wrong").status_code == 429  # one lockout for both
    with voice(client, cookie=False, Authorization=f"Bearer {TOKEN}"):
        pass  # the right token still works


def test_forwarded_host_counts_only_from_the_proxy(make_cloud):
    # Render's proxy may name the public host in X-Forwarded-Host
    _, client = make_cloud()
    login(client)
    with voice(client, origin="https://hey.onrender.com",
               **{"X-Forwarded-Host": "hey.onrender.com"}):
        pass
    # a direct caller can't vouch for an origin with it: the socket ...
    _, direct = make_cloud(peer=PUBLIC)
    login(direct)
    assert refused(voice(direct, origin="https://evil.example",
                         **{"X-Forwarded-Host": "evil.example"})) == 1008
    # ... nor plain requests (the web app's own check never sees it)
    r = direct.post("/api/turn", json={"text": "hi"}, headers={
        "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example"})
    assert r.status_code == 403


def test_web_app_trusts_the_same_proxy_networks():
    from types import SimpleNamespace as NS

    trust = cloud.ProxyNetworks(hops=1, networks=tuple(cloud.parse_networks(TRUSTED)))
    assert webapp.parse_trusted_proxy(trust) is trust and bool(trust)
    assert trust.trusts(NS(client=NS(host="10.204.7.1")))
    assert not trust.trusts(NS(client=NS(host=PUBLIC)))
    assert not trust.trusts(NS(client=None))


def test_voice_page_files_are_public(make_cloud):
    _, client = make_cloud()
    for path in ("/voice.js", "/voice-core.js", "/voice-worklet.js", "/voice.css",
                 "/static/vad/silero_vad_v5.onnx"):
        assert client.get(path, headers=via_proxy(PUBLIC)).status_code == 200, path
    assert client.get("/static/vad/NOTICE.md",
                      headers=via_proxy(PUBLIC)).status_code == 401


def test_cloud_server_caps_websocket_messages(monkeypatch, fake_client):
    import uvicorn

    seen = {}
    monkeypatch.setenv("WEB_TOKEN", TOKEN)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(webapp, "build_assistant", lambda *a, **k: webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS()))
    monkeypatch.setattr(cloud, "warm_up", lambda a: None)
    cfg = load_config(None, None, profile="server")
    cfg["webapp"]["sessions_file"] = ""
    assert cloud.serve(cfg, port=12345) == 0
    assert seen["ws_max_size"] == 2_000_000 and seen["ws"] == "auto"
    assert seen["proxy_headers"] is False and seen["port"] == 12345


# --- slow clients (real uvicorn) ----------------------------------------------

def test_slow_uploads_cannot_starve_the_server(make_cloud):
    """One address holding many slow uploads: /healthz and everyone else keep
    working, that address gets 429 beyond 2 at once, and stalled bodies end
    with 408. (uvicorn's limit_concurrency used to answer 503 to all.)"""
    import http.client
    import socket
    import threading
    import time as _time

    import uvicorn

    guard, _ = make_cloud(guard_kwargs={"body_timeout": 1.0})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        guard, host="127.0.0.1", port=port, lifespan="off", log_level="critical",
        **cloud.UVICORN_OPTIONS))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            _time.sleep(0.05)
        attacker = []
        for _ in range(70):
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(b"POST /api/login HTTP/1.1\r\nHost: t\r\n"
                         b"X-Forwarded-For: 203.0.113.66\r\nX-Forwarded-Proto: https\r\n"
                         b"Content-Type: application/json\r\nContent-Length: 10000\r\n\r\n{")
            attacker.append(sock)
        _time.sleep(0.3)

        def get(path, ip="198.51.100.20"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("GET", path, headers={"X-Forwarded-For": ip,
                                               "X-Forwarded-Proto": "https"})
            r = conn.getresponse()
            body = r.read()
            conn.close()
            return r.status, body

        assert get("/healthz")[0] == 200
        status, page = get("/")
        assert status == 200 and b"</html>" in page  # the whole page
        assert get("/api/state")[0] == 401  # answered, not starved
        # beyond 2 at once, that address is told off right away
        assert b" 429 " in attacker[-1].recv(200)
        # and its stalled bodies time out (1 s + 16 KB at 64 KB/s)
        attacker[0].settimeout(5)
        assert b" 408 " in attacker[0].recv(200)
        assert get("/healthz")[0] == 200
    finally:
        for sock in attacker:
            sock.close()
        server.should_exit = True
        thread.join(timeout=5)


# --- who may vouch for the client address --------------------------------------

def test_nobody_is_a_trusted_proxy_by_default(make_cloud):
    """Behind `docker run -p` every caller arrives from the bridge gateway;
    if private addresses were trusted, X-Forwarded-For would dodge lockouts."""
    _, client = make_cloud(peer="172.17.0.1", guard_kwargs={
        "trusted_proxies": DEFAULTS["cloud"]["trusted_proxies"]})
    assert DEFAULTS["cloud"]["trusted_proxies"] == []
    codes = [login(client, "wrong", ip=f"198.51.100.{i}").status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


def test_server_profile_trusts_only_renders_private_network(monkeypatch):
    cfg = load_config(None, None, profile="server")
    assert cfg["cloud"]["trusted_proxies"] == ["10.0.0.0/8"]
    monkeypatch.setenv("CLOUD_TRUSTED_PROXIES", "10.4.0.0/16, 10.5.0.0/16")
    cfg = load_config(None, None, profile="server")
    nets = cloud.parse_networks(cfg["cloud"]["trusted_proxies"])
    assert [str(n) for n in nets] == ["10.4.0.0/16", "10.5.0.0/16"]


def test_client_ip_header_only_from_the_proxy(make_cloud):
    guard, _ = make_cloud(guard_kwargs={"client_ip_header": "CF-Connecting-IP"})

    def scope(peer, **headers):
        return {"client": (peer, 1), "headers": [
            (k.lower().replace("_", "-").encode(), v.encode()) for k, v in headers.items()]}

    assert guard.client(scope(PROXY, cf_connecting_ip="198.51.100.4",
                              x_forwarded_for="203.0.113.5"))[0] == "198.51.100.4"
    assert guard.client(scope(PROXY, x_forwarded_for="203.0.113.5"))[0] == "203.0.113.5"
    assert guard.client(scope(PUBLIC, cf_connecting_ip="198.51.100.4"))[0] == PUBLIC


def test_ipv6_addresses_count_per_64(make_cloud):
    assert cloud.client_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert cloud.client_key("::ffff:203.0.113.9") == "203.0.113.9"
    _, client = make_cloud()
    for i in range(5):
        login(client, "wrong", ip=f"2001:db8:1:2::{i + 1:x}")
    assert login(client, "wrong", ip="2001:db8:1:2::ffff").status_code == 429


def test_limiter_memory_stays_bounded():
    clock = Clock()
    limiter = cloud.RateLimiter(max_tracked=10, clock=clock)
    lockout = cloud.BoundedLoginLimiter(max_tracked=10, per_ip=1, clock=clock)
    for i in range(500):  # a flood of fresh addresses, all within a minute
        limiter.allow(f"198.51.{i // 256}.{i % 256}")
        lockout.fail(f"198.51.{i // 256}.{i % 256}")
    assert len(limiter.hits) <= 10
    assert max(len(lockout.failures), len(lockout.locked_until)) <= 10


def test_first_requests_log_what_the_proxy_sends(make_cloud, caplog):
    _, client = make_cloud(guard_kwargs={"log_proxy_headers": 2})
    with caplog.at_level(logging.INFO, logger="kwil.cloud"):
        for _ in range(4):
            client.get("/", headers=via_proxy(PUBLIC))
    lines = [r.getMessage() for r in caplog.records if "Proxy check" in r.getMessage()]
    assert len(lines) == 2 and PUBLIC in lines[0] and "trusted" in lines[0]


def test_sign_in_floods_cannot_take_signed_in_slots(make_cloud, fake_client):
    """Review cycle 2: slow sign-ins from 16 IPv6 networks used to fill all
    32 body slots, so Kyle's own requests got 429. Sign-ins now have their
    own small pool (8) and a 2 s body deadline."""
    import http.client
    import json as _json
    import socket
    import threading
    import time as _time

    import uvicorn

    guard, _ = make_cloud()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        guard, host="127.0.0.1", port=port, lifespan="off", log_level="critical",
        **cloud.UVICORN_OPTIONS))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    attacker = []
    try:
        for _ in range(100):
            if server.started:
                break
            _time.sleep(0.05)
        for net in range(16):  # 16 /56s of one /48, 2 slow sign-ins each
            for _ in range(2):
                sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                sock.sendall(
                    b"POST /api/login HTTP/1.1\r\nHost: t\r\n"
                    + f"X-Forwarded-For: 2001:db8:aa:{net:02x}00::1\r\n".encode()
                    + b"X-Forwarded-Proto: https\r\nContent-Type: application/json\r\n"
                      b"Content-Length: 10000\r\n\r\n{")
                attacker.append(sock)
        _time.sleep(0.3)
        assert guard.login_slots.count == 8 and guard.slots.count == 0
        fake_client.queue.append(make_response(text_block("Nothing tomorrow.")))
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/turn", body=_json.dumps({"text": "tomorrow?"}),
                     headers={"Authorization": f"Bearer {TOKEN}",
                              "Content-Type": "application/json",
                              "X-Forwarded-For": "198.51.100.20",
                              "X-Forwarded-Proto": "https"})
        r = conn.getresponse()
        assert r.status == 200 and b"Nothing tomorrow." in r.read()
        conn.close()
        attacker[0].settimeout(5)
        assert b" 408 " in attacker[0].recv(200)  # 2 s for a sign-in body
    finally:
        for sock in attacker:
            sock.close()
        server.should_exit = True
        thread.join(timeout=5)


def test_slots_group_ipv6_by_56_and_locked_addresses_get_one():
    slots = cloud.Slots(per_key=2, total=8)
    a, b = cloud.client_key("2001:db8:1:200::1"), cloud.client_key("2001:db8:1:2ff::1")
    assert a != b and cloud.Slots.key(a) == cloud.Slots.key(b) == "2001:db8:1:200::/56"
    assert slots.take(a) and slots.take(b) and not slots.take(a)
    slots.release(a)
    assert slots.take("198.51.100.1", limit=1) and not slots.take("198.51.100.1", limit=1)


def test_locked_out_addresses_hold_at_most_one_sign_in(make_cloud):
    guard, client = make_cloud()
    for _ in range(5):
        login(client, "wrong")
    assert guard.lockout.blocked(PUBLIC)
    assert guard.login_slots.take(PUBLIC, limit=1)  # what a locked sign-in gets
    r = login(client)  # a second one at the same time: no slot
    assert r.status_code == 429
    guard.login_slots.release(PUBLIC)
    assert login(client).status_code == 200  # the right token still works


def test_eviction_keeps_locked_out_addresses():
    clock = Clock()
    limiter = cloud.BoundedLoginLimiter(max_tracked=10, per_ip=2, clock=clock)
    limiter.fail("192.0.2.1")
    limiter.fail("192.0.2.1")  # locked out
    for i in range(50):
        limiter.fail(f"198.51.100.{i}")  # one wrong token each: not locked
    assert limiter.blocked("192.0.2.1") and len(limiter.failures) <= 10


def test_build_app_refuses_a_web_app_without_its_auth(monkeypatch, fake_client):
    cfg = load_config(None, None, profile="server")
    cfg["webapp"]["sessions_file"] = ""
    monkeypatch.setattr(webapp, "create_app", lambda *a, **k: (lambda s, r, se: None))
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS())
    with pytest.raises(RuntimeError, match="WebAuth"):
        cloud.build_app(cfg, assistant, TOKEN)


# --- family invites (kwil/family.py) ------------------------------------------

@pytest.fixture
def family_cloud(make_cloud, fake_client, tmp_path):
    """CloudGuard around a web app with [family] on; returns (guard, client,
    family)."""
    from kwil.family import Family

    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    fam = Family(config, client=fake_client)
    clock = Clock()
    limiter = cloud.BoundedLoginLimiter(clock=clock)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), clock=clock, family=fam)
    trust = cloud.ProxyNetworks(hops=1, networks=tuple(cloud.parse_networks(TRUSTED)))
    app = webapp.create_app(assistant, TOKEN, limiter=limiter, trusted_proxy=trust,
                            reap_interval=3600)
    guard, client = make_cloud(clock=clock, app=app, guard_kwargs={"lockout": limiter})
    yield guard, client, fam
    fam.close()


def accept(client, invite, ip=PUBLIC):
    return client.post("/api/invite/accept", json={"invite": invite},
                       headers=via_proxy(ip))


def test_invite_accept_passes_the_guard_and_signs_in(family_cloud):
    _, client, fam = family_cloud
    assert client.get("/family.js", headers=via_proxy(PUBLIC)).status_code == 200
    _, token = fam.invite("Mom", "zh")
    r = accept(client, token)
    assert r.status_code == 200, r.text
    state = client.get("/api/state", headers=via_proxy(PUBLIC))
    assert state.status_code == 200 and state.json()["owner"] is False
    # owner-only routes stay hidden from a family member behind the guard too
    assert client.get("/api/users", headers=via_proxy(PUBLIC)).status_code == 404


def test_wrong_invites_lock_out_that_address(family_cloud, slept):
    _, client, fam = family_cloud
    for _ in range(5):
        assert accept(client, "hcinv.guess").status_code == 401
    assert accept(client, "hcinv.guess").status_code == 429
    assert accept(client, "x", ip="198.51.100.7").status_code == 401  # others: as usual
    # the owner's WEB_TOKEN still signs in from the locked-out address
    assert login(client).status_code == 200


# --- family members' own Google and speech (family invites, piece B) -----------------

def test_google_callback_passes_the_guard_without_a_session(fake_client, tmp_path,
                                                            monkeypatch):
    """Google's redirect back carries no session cookie (SameSite=Strict):
    the guard lets it reach google_web.py, which refuses an unknown state
    (400, nothing written) instead of the guard's 401; and build_app wires
    family members' Google in on the server profile."""
    from kwil.family import Family

    cfg = load_config(None, None, profile="server")
    cfg["webapp"].update(sessions_file="", public_origin="https://hey-claude.example.com")
    cfg["family"]["dir"] = str(tmp_path / "users")
    monkeypatch.setenv("GOOGLE_WEB_CLIENT_JSON", json.dumps({"web": {
        "client_id": "c", "client_secret": "s", "auth_uri": "https://a",
        "token_uri": "https://t"}}))
    fam = Family(cfg, client=fake_client)
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS(), family=fam)
    guard = cloud.build_app(cfg, assistant, TOKEN)
    client = TestClient(at(PROXY, guard), base_url="https://testserver")
    r = client.get("/google/callback", params={"state": "made-up", "code": "c"},
                   headers=via_proxy(PUBLIC), follow_redirects=False)
    assert r.status_code == 400 and "Google" in r.text
    assert not (tmp_path / "users").exists() or not any((tmp_path / "users").iterdir())
    # the rest still needs signing in
    assert client.get("/api/google/status", headers=via_proxy(PUBLIC)).status_code == 401
    # the signed-out page carries data-google ([webapp] owner_google), so
    # i18n.js swaps in the invitees' login line ("Enter the sign-in code you
    # were sent..."; tests/js/i18n.test.mjs) and stops polling on the 401
    page = client.get("/", headers=via_proxy(PUBLIC))
    assert page.status_code == 200
    assert '<html lang="en" data-google>' in page.text and 'id="login-text"' in page.text
    _, invite = fam.invite("Mom", "zh", tier="family")
    assert accept(client, invite).status_code == 200
    status = client.get("/api/google/status", headers=via_proxy(PUBLIC)).json()
    assert status == {"enabled": True, "connected": False, "disconnect": True,
                      "gmail": True, "note": None,
                      "needs_reconnect": False, "server_owner": False}
    fam.close()


def test_notion_callback_passes_the_guard_without_a_session(fake_client, tmp_path,
                                                            monkeypatch):
    """Notion's redirect back carries no session cookie either: the guard
    lets it reach notion_web.py, which refuses a made-up state (400,
    nothing written, never a call to Notion); its page code is public, its
    API still needs signing in."""
    import httpx

    from kwil import notion_web
    from kwil.family import Family

    def no_network(request):
        raise AssertionError("a test tried to reach Notion")

    monkeypatch.setattr(notion_web, "TRANSPORT", httpx.MockTransport(no_network))
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("NOTION_OAUTH_CLIENT_SECRET", "secret_" + "c" * 43)
    monkeypatch.setenv("USER_DATA_KEY", "k" * 48)
    cfg = load_config(None, None, profile="server")
    cfg["webapp"].update(sessions_file="", public_origin="https://hey-claude.example.com")
    cfg["family"]["dir"] = str(tmp_path / "users")
    fam = Family(cfg, client=fake_client)
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS(), family=fam)
    guard = cloud.build_app(cfg, assistant, TOKEN)
    client = TestClient(at(PROXY, guard), base_url="https://testserver")
    r = client.get("/notion/callback", params={"state": "made-up", "code": "c"},
                   headers=via_proxy(PUBLIC), follow_redirects=False)
    assert r.status_code == 400 and "Notion" in r.text
    assert not (tmp_path / "users").exists() or not any((tmp_path / "users").iterdir())
    assert client.get("/notion.js", headers=via_proxy(PUBLIC)).status_code == 200
    assert client.get("/api/notion/status", headers=via_proxy(PUBLIC)).status_code == 401
    _, invite = fam.invite("Tess", "en", tier="beta")
    assert accept(client, invite).status_code == 200
    status = client.get("/api/notion/status", headers=via_proxy(PUBLIC)).json()
    assert status == {"enabled": True, "connected": False, "workspace": None,
                      "database": None}
    fam.close()


def _render_yaml() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "render.yaml").read_text(encoding="utf-8")


def _dockerfile() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")


def test_render_and_docker_are_ready_for_family():
    import re
    from pathlib import PurePosixPath

    from kwil.config import ENV_OVERRIDES, SERVER_PROFILE
    from kwil.tools.google_auth import WEB_CLIENT_ENV

    render, docker = _render_yaml(), _dockerfile()
    # family accounts live on the persistent disk
    mount = re.search(r"mountPath:\s*(\S+)", render).group(1)
    assert PurePosixPath(SERVER_PROFILE["family"]["dir"]).is_relative_to(mount)
    assert SERVER_PROFILE["family"]["enabled"] is True
    # the settings family Google and Chinese speech need are offered
    keys = set(re.findall(r"-\s*key:\s*(\w+)", render))
    assert {"PUBLIC_ORIGIN", WEB_CLIENT_ENV, "ZH_WHISPER_MODEL"} <= keys
    assert ENV_OVERRIDES["PUBLIC_ORIGIN"] == ("webapp", "public_origin")
    assert ENV_OVERRIDES["ZH_WHISPER_MODEL"] == ("family", "zh_stt_model")
    assert re.search(rf"key: {WEB_CLIENT_ENV}\s*(#.*)?\n\s*sync: false", render)  # a secret
    zh_model = re.search(r"key: ZH_WHISPER_MODEL.*\n\s*value: (\S+)", render).group(1)
    # the image bakes in the Chinese Whisper model and voice the config names
    # (HF_HUB_OFFLINE=1: nothing can be fetched at run time)
    assert "HF_HUB_OFFLINE=1" in docker
    assert re.search(rf"ARG ZH_WHISPER_MODEL={zh_model}\n", docker)
    assert zh_model == DEFAULTS["family"]["zh_stt_model"]
    voice = PurePosixPath(DEFAULTS["family"]["zh_piper_voice"])
    assert re.search(rf"ARG ZH_PIPER_VOICE={voice.stem}\n", docker)
    assert f"/app/{voice.parent}" in docker  # WORKDIR /app + voices/
    assert re.search(r"ARG FAMILY_ZH=1\n", docker)
    assert '"$ZH_PIPER_VOICE_URL" "$ZH_PIPER_VOICE"' in docker
    assert "$ZH_WHISPER_MODEL" in docker
