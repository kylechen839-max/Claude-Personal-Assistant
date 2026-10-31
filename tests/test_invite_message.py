"""The invite text the owner sends (family.invite_message, in POST
/api/invites and a new link's answer): the invitee's language, and for a
beta tester the key guide, Google's "unverified" step, the starter credit
and the template and chat links (https only, hidden when unset)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from kwil import i18n  # noqa: E402
from tests.test_userkeys import build_env  # noqa: E402
from tests.test_webapp import login  # noqa: E402

TEMPLATE = "https://five-leo-17d.notion.site/Kwil-Assignments-template-40af8b8e230381148083f5740cc8ad4a"
CHAT = "https://chat.example/join/abc"
UNVERIFIED = "Google will say the app is unverified: tap Advanced → Go to Kwil " \
             "(that's expected)."


@pytest.fixture
def env(tmp_path, monkeypatch):
    # the code defaults: no template or chat link
    e = build_env(tmp_path, monkeypatch, notion_template_url="", beta_chat_url="")
    yield e
    e.close()


@pytest.fixture
def owner(env):
    c = env.client()
    login(c)
    return c


def invite(owner, **body):
    r = owner.post("/api/invites", json={"name": "Tess", "language": "en", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_beta_invite_in_english_with_the_links_unset(env, owner):
    out = invite(owner)
    msg = out["message"]
    assert out["url"] in msg and msg.startswith("Hi Tess!")
    assert UNVERIFIED in msg
    assert "https://testserver/help/anthropic-key" in msg
    assert "$5" in msg  # the starter credit
    assert "notion" not in msg.lower() and "chat" not in msg.lower()


def test_links_are_shown_only_when_https(env, owner):
    f = env.fam.config["family"]
    f.update(notion_template_url=TEMPLATE, beta_chat_url="http://chat.example/x")
    msg = invite(owner)["message"]
    assert i18n.t("invite_template", "en", url=TEMPLATE) in msg
    assert "chat.example" not in msg
    f.update(notion_template_url="not a link", beta_chat_url=CHAT)
    msg = invite(owner)["message"]
    assert "not a link" not in msg and "Notion" not in msg
    assert i18n.t("invite_chat", "en", url=CHAT) in msg


def test_a_beta_invite_in_chinese(env, owner):
    env.fam.config["family"].update(notion_template_url=TEMPLATE, beta_chat_url=CHAT)
    out = invite(owner, language="zh")
    msg = out["message"]
    assert msg.startswith("Tess你好") and out["url"] in msg
    assert "未经验证" in msg and "前往 Kwil" in msg
    assert i18n.t("invite_template", "zh", url=TEMPLATE) in msg
    assert i18n.t("invite_chat", "zh", url=CHAT) in msg
    assert "Google will say" not in msg


def test_a_family_invite_is_only_the_link(env, owner):
    env.fam.config["family"].update(notion_template_url=TEMPLATE, beta_chat_url=CHAT)
    out = invite(owner, tier="family")
    assert out["message"] == i18n.t("invite_family", "en", name="Tess", url=out["url"])
    assert "unverified" not in out["message"] and TEMPLATE not in out["message"]
    zh = invite(owner, tier="family", language="zh")
    assert zh["message"] == i18n.t("invite_family", "zh", name="Tess", url=zh["url"])


def test_a_new_link_gets_a_new_message_and_no_credit_line_when_off(env, owner):
    out = invite(owner)
    uid = out["user"]["id"]
    env.fam.config["family"]["beta_credit_usd"] = 0
    from kwil.credit import CreditLedger

    env.fam.credit = CreditLedger(env.fam.config, env.fam.dir)
    again = owner.post(f"/api/users/{uid}/invite", json={}).json()
    assert again["url"] in again["message"] and again["url"] != out["url"]
    assert "$" not in again["message"]
