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

from assistant import cloud, doctor, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import DEFAULTS, load_config  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402
from tests.test_webapp import TOKEN, Clock, FakeTranscriber, wav_bytes  # noqa: E402

PROXY = "10.1.2.3"        # Render's TLS proxy reaches the container privately
PUBLIC = "203.0.113.9"    # someone on the internet connecting directly
TRUSTED = DEFAULTS["cloud"]["trusted_proxies"]


def at(peer, app):
    """The app as seen from `peer` (the TCP address the server reports)."""
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer, 40000)}
        await app(scope, receive, send)
    return wrapped


@pytest.fixture
def make_cloud(fake_client):
    def make(peer=PROXY, clock=None, guard_kwargs=None, app=None):
        clock = clock or Clock()
        if app is None:
            assistant = webapp.WebAssistant(
                lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
                FakeTranscriber("what's on my calendar"), PrintTTS(), clock=clock)
            app = webapp.create_app(assistant, TOKEN, limiter=cloud.NeverBlock(),
                                    reap_interval=3600)
        kwargs = {"trusted_proxies": TRUSTED, "max_upload_bytes": 200_000,
                  "max_body_bytes": 16_384,
                  "lockout": cloud.LoginLockout(clock=clock),
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
    assert r.status_code == 200 and r.text == "ok"
    assert "server" not in r.headers and "set-cookie" not in r.headers
    assert r.headers["cache-control"] == "no-store"
    # health checks come over plain http from inside Render: no redirect
    assert client.get("/healthz", headers=via_proxy(PUBLIC, "http")).status_code == 200
    assert client.post("/healthz").status_code == 401  # only GET/HEAD


# --- the auth wall ------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/app.js", "/app.css", "/sw.js",
                                  "/manifest.webmanifest", "/icon.svg",
                                  "/icon-192.png", "/icon-512.png", "/icon-180.png"])
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
    assert client.get("/api/state", headers=ok).status_code == 429
    assert login(client).status_code == 429  # same address, same lockout


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

def test_wrong_tokens_lock_out_that_address_only(make_cloud):
    clock = Clock()
    _, client = make_cloud(clock=clock)
    for _ in range(5):
        assert login(client, "wrong" * 10).status_code == 401
    r = login(client)  # even the right token: locked
    assert r.status_code == 429 and int(r.headers["retry-after"]) == 900
    assert login(client, ip="198.51.100.7").status_code == 200  # Kyle elsewhere
    clock.now += 901
    assert login(client).status_code == 200


def test_lockouts_double_and_success_resets(make_cloud):
    clock = Clock()
    guard, client = make_cloud(clock=clock)
    for round_, expected in ((1, 900), (2, 1800), (3, 3600)):
        for _ in range(5):
            login(client, "bad-token")
        assert guard.lockout.retry_after(PUBLIC) == expected, round_
        clock.now += expected + 1
    assert login(client).status_code == 200
    for _ in range(5):
        login(client, "bad-token")
    assert guard.lockout.retry_after(PUBLIC) == 900  # back to the first step


def test_signed_in_phone_keeps_working_during_a_lockout(make_cloud):
    _, client = make_cloud()
    assert login(client).status_code == 200
    for _ in range(5):
        client.post("/api/login", json={"token": "nope"}, headers=via_proxy(PUBLIC))
    assert client.get("/api/state", headers=via_proxy(PUBLIC)).status_code == 200


def test_global_cap_across_many_addresses(make_cloud):
    _, client = make_cloud(guard_kwargs={"lockout": cloud.LoginLockout(
        max_failures=100, global_max_failures=10, clock=Clock())})
    for i in range(10):
        assert login(client, "bad", ip=f"198.51.100.{i}").status_code == 401
    assert login(client, ip="192.0.2.1").status_code == 429


def test_the_web_apps_own_global_limiter_is_not_used(make_cloud):
    """Otherwise 5 wrong tokens from anyone would lock Kyle out too."""
    assert cloud.NeverBlock().blocked() is False


def test_spoofed_forwarded_for_does_not_dodge_the_lockout(make_cloud):
    # the client adds its own X-Forwarded-For; the proxy appends the real one
    _, client = make_cloud()
    for i in range(5):
        client.post("/api/login", json={"token": "bad"},
                    headers=via_proxy(f"192.0.2.{i}, {PUBLIC}"))
    assert client.post("/api/login", json={"token": TOKEN},
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
        raise RuntimeError("secret internals at /app/assistant/x.py line 3")

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
    assistant = webapp.WebAssistant(lambda reg: ClaudeChat(client=fake_client,
                                                           model="m", tools=reg),
                                    FakeTranscriber(), PrintTTS())
    guard = cloud.build_app(cfg, assistant, TOKEN)
    assert isinstance(guard, cloud.CloudGuard)
    assert guard.max_upload_bytes == 4_000_000 and guard.max_body_bytes == 16_384
    assert guard.lockout.max_failures == 5 and guard.limiter.per_minute == 120
    client = TestClient(at(PROXY, guard), base_url="https://testserver")
    assert client.get("/healthz").text == "ok"


def test_cli_web_mode_on_a_server_runs_the_cloud_app(monkeypatch, tmp_path,
                                                     restore_root_logging):
    from assistant import cli

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
            "from assistant import cli, cloud, webapp\n"
            "from assistant.config import load_config\n"
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
