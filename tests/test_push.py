"""Web Push (kwil/push.py): subscriptions sealed at rest per person,
the VAPID key made once, sending through pywebpush (patched: no network),
a push service's 410 forgets the device, and endpoints only at real push
services. Logs never carry a notification's text at INFO."""

import base64
import json
import logging

import pytest

pytest.importorskip("cryptography")
pywebpush = pytest.importorskip("pywebpush")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from kwil import family as family_mod, push as push_mod  # noqa: E402
from kwil.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS, load_config  # noqa: E402

SECRET = "p" * 48  # USER_DATA_KEY
APPLE = "https://web.push.apple.com/QGuQyavXutnMcmwhxyz-abc_DEF"
FCM = "https://fcm.googleapis.com/fcm/send/dXJ4Yz:APA91bH-example"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABexample"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def browser_keys() -> dict:
    """A subscription's keys as a browser makes them (P-256 point, 16-byte auth)."""
    key = ec.generate_private_key(ec.SECP256R1())
    point = key.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": b64(point), "auth": b64(b"0123456789abcdef")}


def sub(endpoint=APPLE) -> dict:
    """PushSubscription.toJSON() (expirationTime is in the real shape)."""
    return {"endpoint": endpoint, "expirationTime": None, "keys": browser_keys()}


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.status = status
        self.reason = "Gone" if status == 410 else "Error"
        self.text = ""
        self.headers = {}


class Sent:
    """pywebpush.webpush stand-in: records each call; raises like it does."""

    def __init__(self):
        self.calls = []
        self.fail = {}  # endpoint -> HTTP status

    def __call__(self, **kw):
        self.calls.append(kw)
        status = self.fail.get(kw["subscription_info"]["endpoint"])
        if status:
            raise pywebpush.WebPushException(f"Push failed: {status}",
                                              response=FakeResponse(status))
        return FakeResponse(201)


@pytest.fixture
def config(tmp_path, monkeypatch):
    for var in ("PUBLIC_ORIGIN", push_mod.VAPID_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    config = load_config(None, None, profile="")
    config["push"]["owner_dir"] = str(tmp_path / "student")
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["webapp"]["public_origin"] = "https://hey-claude.example.com"
    return config


@pytest.fixture
def sent():
    return Sent()


@pytest.fixture
def push(config, sent):
    return push_mod.Push(config, send_fn=sent)


def test_defaults_and_secret_env():
    config = load_config(None, None, profile="")
    assert config["push"]["max_devices"] == 5 and config["push"]["enabled"] is True
    assert "VAPID_PRIVATE_KEY" in SECRET_ENV_VARS and "VAPID_PRIVATE_KEY" in ISOLATED_ENV_VARS
    server = load_config(None, None, profile="server")
    assert server["push"]["owner_dir"] == "/data/student"  # on Render's disk


def test_subscription_is_sealed_at_rest_and_reads_back(push, config, tmp_path):
    s = sub()
    assert push.subscribe(None, s) == 1
    path = tmp_path / "student" / push_mod.PUSH_FILE
    raw = path.read_text()
    assert "apple" not in raw and s["keys"]["auth"] not in raw and s["keys"]["p256dh"] not in raw
    [saved] = push.subscriptions(None)
    assert saved["endpoint"] == APPLE and saved["keys"] == s["keys"]
    assert "expirationTime" not in saved
    assert push.has_devices(None)


def test_nothing_is_stored_without_user_data_key(push, tmp_path, monkeypatch):
    monkeypatch.delenv("USER_DATA_KEY")
    with pytest.raises(push_mod.PushProblem) as e:
        push.subscribe(None, sub())
    assert e.value.status == 503
    assert not (tmp_path / "student" / push_mod.PUSH_FILE).exists()


@pytest.mark.parametrize("endpoint", [
    "http://web.push.apple.com/x",                 # not https
    "https://169.254.169.254/latest/meta-data",    # the cloud's metadata service
    "https://127.0.0.1/api/turn",                  # this server
    "https://evil.example.com/push",
    "https://web.push.apple.com.evil.example/x",   # a look-alike host
    "https://user:pw@fcm.googleapis.com/fcm/send/x",
    "https://fcm.googleapis.com:8443/fcm/send/x",
    "https://fcm.googleapis.com/" + "x" * 3000,
])
def test_only_known_push_services_are_accepted(push, endpoint, sent):
    with pytest.raises(push_mod.PushProblem) as e:
        push.subscribe(None, sub(endpoint))
    assert e.value.status == 400 and endpoint not in str(e.value)
    assert push.subscriptions(None) == []


@pytest.mark.parametrize("bad", [None, "x", {"endpoint": APPLE},
                                 {"endpoint": APPLE, "keys": {"p256dh": "a b", "auth": "x"}},
                                 # a port urlsplit can't read: 400, not a 500
                                 sub("https://fcm.googleapis.com:99999/fcm/send/x"),
                                 sub("https://fcm.googleapis.com:abc/fcm/send/x")])
def test_malformed_subscriptions_are_refused(push, bad):
    with pytest.raises(push_mod.PushProblem):
        push.subscribe(None, bad)


@pytest.mark.parametrize("endpoint", [APPLE, FCM, MOZ,
                                      "https://wns2-par02p.notify.windows.com/w/?token=x"])
def test_the_real_push_services_are_accepted(push, endpoint):
    assert push.subscribe(None, sub(endpoint)) == 1


def test_at_most_five_devices_and_a_resubscribe_replaces(push, config):
    for i in range(7):
        push.subscribe(None, sub(f"{FCM}-{i}"))
    ends = [s["endpoint"] for s in push.subscriptions(None)]
    assert ends == [f"{FCM}-{i}" for i in range(2, 7)]  # the oldest two dropped
    push.subscribe(None, sub(f"{FCM}-3"))
    ends = [s["endpoint"] for s in push.subscriptions(None)]
    assert len(ends) == 5 and ends[-1] == f"{FCM}-3" and ends.count(f"{FCM}-3") == 1


def test_vapid_key_is_made_once_and_kept_private(config, tmp_path, sent):
    first = push_mod.Push(config, send_fn=sent).public_key()
    raw = base64.urlsafe_b64decode(first + "==")
    assert len(raw) == 65 and raw[0] == 4  # an uncompressed P-256 point
    path = tmp_path / "student" / push_mod.VAPID_FILE
    assert path.exists()
    import os
    import sys
    if sys.platform != "win32":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert push_mod.Push(config, send_fn=sent).public_key() == first  # restart: same key


def test_vapid_private_key_from_the_environment_wins(config, tmp_path, monkeypatch, sent):
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv(push_mod.VAPID_ENV,
                       b64(key.private_numbers().private_value.to_bytes(32, "big")))
    public = push_mod.Push(config, send_fn=sent).public_key()
    expected = b64(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    assert public == expected
    assert not (tmp_path / "student" / push_mod.VAPID_FILE).exists()


def test_send_signs_with_vapid_and_sends_the_payload(push, sent):
    push.subscribe(None, sub(APPLE))
    push.subscribe(None, sub(FCM))
    assert push.send(None, "Hey Claude", "Essay due tomorrow", "/#brief") == 2
    first = sent.calls[0]
    assert json.loads(first["data"]) == {"title": "Hey Claude", "body": "Essay due tomorrow",
                                         "url": "/#brief"}
    assert first["vapid_claims"] == {"sub": "https://hey-claude.example.com"}
    assert first["ttl"] == 86400 and first["timeout"] == 10
    assert set(first["subscription_info"]) == {"endpoint", "keys"}


def test_send_never_points_the_tap_off_site(push, sent):
    push.subscribe(None, sub())
    bad = ["https://evil.example/", "//evil.example/", "/\t/evil.example", "/\n/evil.example",
           "/\\evil.example", "/\r\n/evil.example"]
    for url in bad:
        push.send(None, "t", "b", url)
    push.send(None, "t", "b", "/?x=1#brief")
    assert [json.loads(c["data"])["url"] for c in sent.calls] == ["/#brief"] * len(bad) + [
        "/?x=1#brief"]



def test_a_gone_device_is_forgotten_and_others_kept(push, sent):
    push.subscribe(None, sub(APPLE))
    push.subscribe(None, sub(FCM))
    sent.fail[APPLE] = 410
    assert push.send(None, "t", "b") == 1
    assert [s["endpoint"] for s in push.subscriptions(None)] == [FCM]
    sent.fail = {FCM: 500}  # a server error: tried again next time
    assert push.send(None, "t", "b") == 0
    assert [s["endpoint"] for s in push.subscriptions(None)] == [FCM]
    sent.fail = {FCM: 404}
    push.send(None, "t", "b")
    assert push.subscriptions(None) == [] and not push.has_devices(None)


def test_real_pywebpush_encrypts_and_signs_our_subscription(config, push, tmp_path,
                                                             monkeypatch):
    """No network: pywebpush's curl mode builds the real request (aes128gcm
    body, VAPID Authorization header) from what we pass it. (It writes the
    body to ./encrypted.data, so run it in tmp_path.)"""
    monkeypatch.chdir(tmp_path)
    push.subscribe(None, sub(FCM))
    [saved] = push.subscriptions(None)
    out = pywebpush.webpush(subscription_info={"endpoint": saved["endpoint"],
                                               "keys": saved["keys"]},
                            data=json.dumps({"title": "t", "body": "b", "url": "/#brief"}),
                            vapid_private_key=push.vapid(),
                            vapid_claims={"sub": push.claims_subject()}, curl=True)
    assert "vapid t=" in out and "aes128gcm" in out and FCM in out


def test_unsubscribe_deletes_the_file_when_none_left(push, tmp_path):
    push.subscribe(None, sub(APPLE))
    assert push.unsubscribe(None, "https://web.push.apple.com/other") is False
    assert push.unsubscribe(None, APPLE) is True
    assert not (tmp_path / "student" / push_mod.PUSH_FILE).exists()


def test_each_person_has_their_own_sealed_file(config, sent, tmp_path):
    fam = family_mod.Family(config)
    mine, _ = fam.invite("Tess")
    other, _ = fam.invite("Ann")
    p = push_mod.Push(config, family=fam, send_fn=sent)
    p.subscribe(mine["id"], sub(APPLE))
    theirs = tmp_path / "users" / mine["id"] / push_mod.PUSH_FILE
    assert theirs.exists()
    assert p.subscriptions(other["id"]) == [] and p.subscriptions(None) == []
    # a file copied into someone else's folder doesn't open as theirs
    (tmp_path / "users" / other["id"]).mkdir(parents=True, exist_ok=True)
    (tmp_path / "users" / other["id"] / push_mod.PUSH_FILE).write_bytes(theirs.read_bytes())
    assert p.subscriptions(other["id"]) == []
    with pytest.raises(push_mod.PushProblem) as e:
        p.subscribe("u_notlisted1234", sub())
    assert e.value.status == 404
    fam.remove(mine["id"])  # their folder, subscriptions included, is deleted
    assert not theirs.exists() and p.subscriptions(mine["id"]) == []
    fam.close()


def test_logs_never_carry_the_notification_text_or_endpoint(push, sent, caplog):
    caplog.set_level(logging.DEBUG, logger="kwil.push")
    push.subscribe(None, sub(APPLE))
    sent.fail[APPLE] = 410
    push.subscribe(None, sub(FCM))
    push.send(None, "Chem midterm", "Review chapter 7 tonight", "/#brief")
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert info and not any("Chem" in m or "chapter" in m or "push.apple" in m
                            or "googleapis" in m for m in info)
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Review chapter 7" in m for m in debug)


def test_requirements_pin_pywebpush_and_a_cryptography_it_supports():
    from pathlib import Path

    reqs = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
    lines = [line.split("#")[0].strip() for line in reqs.splitlines()]
    assert "pywebpush>=2.5" in lines and "cryptography>=47" in lines
