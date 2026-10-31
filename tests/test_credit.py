"""The beta's starter credit (kwil/credit.py, family.Family.api_for and
UserApi): a beta tester without a key runs on the owner's key until their
credit (or everyone's) is used up, then hears "credit_spent".

All offline: the owner's client is test_userkeys' mocked Anthropic client,
answering with usage in the SDK's shape, so each call's cost is known."""

import json
import logging
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from kwil import credit as credit_mod, family as family_mod, i18n, usage, userkeys  # noqa: E402
from kwil.cli import friendly_error  # noqa: E402
from kwil.config import load_config  # noqa: E402
from tests.conftest import make_response, make_usage, text_block  # noqa: E402
from tests.test_userkeys import KEY_A, build_env, by_key, put  # noqa: E402

# Haiku 5.5 (the router's pick for "hello") over 100K tokens: 5 x $0.10/MTok,
# so 2M input tokens are exactly $1.00 a call
ONE_DOLLAR = make_usage(2_000_000, 0)


def answer(env, n=1, usage_=ONE_DOLLAR):
    for _ in range(n):
        env.owner_api.queue.append(make_response(text_block("Hi."), usage=usage_))


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    """test_userkeys' app with the starter credit on (the defaults: $5 each,
    $50 everyone), or the settings a test passes (indirect parametrize)."""
    e = build_env(tmp_path, monkeypatch,
                  **{"turns_per_minute": 0, **getattr(request, "param", {})})
    yield e
    e.close()


@pytest.fixture
def owner(env):
    from tests.test_webapp import login

    c = env.client()
    login(c)
    return c


def test_a_beta_turn_runs_on_the_owners_key_until_five_dollars(env, owner, caplog):
    tess, tess_id = env.member(owner, "Tess")
    assert env.fam.credit.enabled and env.fam.credit.per_person == 5
    answer(env, 6)
    with caplog.at_level(logging.DEBUG):
        for _ in range(5):
            assert env.reply(tess, "hello SECRETWORD") == "Hi."
    assert len(env.owner_api.calls) == 5 and env.made == []
    assert env.fam.credit.used(tess_id) == pytest.approx(5.0)
    assert usage.month_usd() == pytest.approx(5.0)  # on the owner's bill
    # the 6th: told, and nothing sent
    said = env.reply(tess, "hello")
    assert said == i18n.t("credit_spent", usd="5")
    assert "$5 starter credit is used up" in said and "Settings" in said
    assert len(env.owner_api.calls) == 5
    with pytest.raises(userkeys.CreditSpent):
        env.fam.client(tess_id)
    # their Settings
    st = tess.get("/api/me/anthropic-key").json()
    assert st["credit_usd"] == 5 and st["credit_left_usd"] == 0
    # INFO has ids and amounts, never what they said
    assert "SECRETWORD" not in "".join(r.getMessage() for r in caplog.records
                                       if r.levelno >= logging.INFO)
    assert any(r.name == "kwil.credit" and tess_id in r.getMessage()
               for r in caplog.records)
    # with their own key, the next turn is on it and costs the credit nothing
    assert put(tess, KEY_A).status_code == 200
    assert env.reply(tess) == "Hi."
    assert len(by_key(env, KEY_A)[0].calls) == 1 and len(env.owner_api.calls) == 5
    assert env.fam.credit.used(tess_id) == pytest.approx(5.0)


def test_a_family_member_without_a_key_is_never_counted(env, owner):
    mom, mom_id = env.member(owner, "Mom", tier="family")
    answer(env, 7)
    for _ in range(7):
        assert env.reply(mom) == "Hi."
    assert env.fam.credit.used(mom_id) == 0 and env.fam.credit.total == 0
    assert "credit_usd" not in mom.get("/api/me/anthropic-key").json()


@pytest.mark.parametrize("env", [{"beta_credit_total_usd": 1.2}], indirect=True)
def test_the_global_cap_stops_a_second_tester(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    ugo, ugo_id = env.member(owner, "Ugo")
    answer(env, 3)
    assert env.reply(tess) == "Hi."  # $1 of everyone's $1.20
    assert env.reply(ugo) == i18n.t("credit_spent", usd="5")  # $1.39 would overshoot
    assert env.reply(tess) == i18n.t("credit_spent", usd="5")
    assert len(env.owner_api.calls) == 1
    assert env.fam.credit.used(ugo_id) == 0
    # Settings never shows more than what's left of everyone's
    assert ugo.get("/api/me/anthropic-key").json()["credit_left_usd"] == pytest.approx(0.2)
    # the owner's list
    listed = owner.get("/api/users").json()
    assert listed["credit_total_used"] == pytest.approx(1.0)
    assert listed["credit_total_cap"] == pytest.approx(1.2)
    used = {u["id"]: u["credit_used_usd"] for u in listed["users"]}
    assert used == {tess_id: pytest.approx(1.0), ugo_id: 0}


def test_a_restart_keeps_what_was_used(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    answer(env, 2)
    env.reply(tess)
    env.reply(tess)
    path = env.tmp / "users" / "beta_credit.json"
    saved = json.loads(path.read_text())
    assert saved == {"total_usd": pytest.approx(2.0), "people": {tess_id: pytest.approx(2.0)}}
    again = family_mod.Family(env.config, client=env.owner_api)
    assert again.credit.used(tess_id) == pytest.approx(2.0)
    assert again.credit.total == pytest.approx(2.0)
    assert again.credit.left(tess_id) == pytest.approx(3.0)
    again.close()


def test_deleting_a_person_keeps_everyones_total(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    answer(env, 1)
    env.reply(tess)
    assert owner.post(f"/api/users/{tess_id}/remove", json={}).status_code == 200
    saved = json.loads((env.tmp / "users" / "beta_credit.json").read_text())
    assert saved["people"] == {} and saved["total_usd"] == pytest.approx(1.0)
    assert env.fam.credit.total == pytest.approx(1.0)
    # a call that was in flight settles into the total, not a revived row
    env.fam.credit.settle(tess_id, 0.5)
    assert tess_id not in env.fam.credit.people
    assert env.fam.credit.total == pytest.approx(1.5)
    # and a new invite starts from what's left of everyone's
    ugo, ugo_id = env.member(owner, "Ugo")
    assert env.fam.credit.left(ugo_id) == pytest.approx(5.0)
    assert owner.get("/api/users").json()["credit_total_used"] == pytest.approx(1.5)


def ledger(tmp_path, per_person=1.0, cap=50.0, clock=None):
    config = load_config(None, None)
    config["family"].update(dir=str(tmp_path), beta_credit_usd=per_person,
                            beta_credit_total_usd=cap)
    kw = {"monotonic": clock} if clock is not None else {}
    return credit_mod.CreditLedger(config, tmp_path, **kw)


def test_the_reserve_is_one_calls_worst_case(tmp_path):
    book = ledger(tmp_path)
    a = load_config(None, None)["assistant"]
    worst = usage.cost(a["model"], {"input": a["max_history_chars"] // 3,
                                    "output": a["think_max_tokens"],
                                    "cache_read": 0, "cache_write": 0})
    assert book.reserve == pytest.approx(worst + 3 * credit_mod.WEB_SEARCH_USD)
    assert book.ttl == a["api_timeout"] * (a["api_max_retries"] + 1)


@pytest.mark.parametrize("per_person,cap,uids", [(1.0, 50.0, ["u_same00001"] * 8),
                                                 (50.0, 1.0, [f"u_other0000{i}"
                                                              for i in range(8)])])
def test_parallel_admits_never_overshoot(tmp_path, per_person, cap, uids):
    book = ledger(tmp_path, per_person, cap)
    fits = int(min(per_person, cap) // book.reserve)
    assert 1 <= fits < 8
    barrier = threading.Barrier(len(uids))
    tokens = []

    def go(uid):
        barrier.wait()
        tokens.append(book.admit(uid))

    threads = [threading.Thread(target=go, args=(u,)) for u in uids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([t for t in tokens if t is not None]) == fits
    # settled at the worst case, the money spent is within the limit
    for uid, token in zip(uids, tokens):
        if token is not None:
            book.settle(uid, book.reserve, token)
    assert book.total <= min(per_person, cap) + 1e-9


def test_a_failed_calls_reservation_expires(tmp_path):
    now = [0.0]
    book = ledger(tmp_path, per_person=book_size(), clock=lambda: now[0])
    assert book.admit("u_abcdefgh1") is not None
    assert book.admit("u_abcdefgh1") is None  # one in flight fills it
    now[0] += book.ttl + 1  # never settled (the call failed)
    assert book.admit("u_abcdefgh1") is not None


def book_size():
    a = load_config(None, None)["assistant"]
    one = credit_mod.call_cost(a["model"], {
        "input": a["max_history_chars"] // 3, "output": a["think_max_tokens"],
        "cache_read": 0, "cache_write": 0, "web_searches": 3})
    return one * 1.5  # room for one reservation, not two


def test_an_unreadable_ledger_fails_closed(tmp_path, caplog):
    (tmp_path / credit_mod.FILE).write_text("{not json")
    with caplog.at_level(logging.ERROR):
        book = ledger(tmp_path, per_person=5)
    assert book.broken and book.admit("u_abcdefgh1") is None
    assert book.left("u_abcdefgh1") == 0 and "unreadable" in caplog.text


def test_off_means_no_key_as_before(tmp_path):
    for per_person, cap in ((0, 50), (5, 0)):
        book = ledger(tmp_path, per_person, cap)
        assert not book.enabled and book.admit("u_abcdefgh1") is None


def test_the_sentence_says_the_amount_and_links_the_guide_if_there_is_one():
    plain = friendly_error(userkeys.CreditSpent(5.0), output_fn=lambda m: None)
    assert plain == i18n.t("credit_spent", usd="5") and "$5" in plain
    url = "https://kwil.example/help/anthropic-key"
    linked = friendly_error(userkeys.CreditSpent(2.5, url), output_fn=lambda m: None)
    assert linked.endswith(url) and "$2.50" in linked
    with i18n.using("zh"):
        zh = friendly_error(userkeys.CreditSpent(5.0), output_fn=lambda m: None)
    assert zh == i18n.ZH["credit_spent"].format(usd="5") and "5 美元" in zh
    for key in ("credit_spent", "credit_spent_help"):
        assert i18n.EN[key] != i18n.ZH[key]
    # it is a MissingKey: everything that says "no key" still stops
    assert issubclass(userkeys.CreditSpent, userkeys.MissingKey)


def test_the_help_link_needs_the_public_site_and_an_origin(tmp_path):
    book = ledger(tmp_path)
    config = load_config(None, None)
    config["webapp"].update(public_site=False, public_origin="https://kwil.example")
    assert book.help_url(config) == ""
    config["webapp"]["public_site"] = True
    assert book.help_url(config) == "https://kwil.example/help/anthropic-key"


def test_the_voice_path_says_credit_spent():
    from kwil.cli import user_error_key, user_error_text

    e = userkeys.CreditSpent(5.0)
    assert user_error_key(e) == "credit_spent"
    assert user_error_text(e, "credit_spent", "zh") == i18n.ZH["credit_spent"].format(usd="5")


def test_quotas_still_apply_on_the_credit(env, owner):
    env.fam.quotas.turns_per_minute = 2
    tess, _ = env.member(owner, "Tess")
    answer(env, 3, make_usage(1000, 100))
    assert env.reply(tess) == "Hi." and env.reply(tess) == "Hi."
    assert env.reply(tess) == i18n.t("limit_minute", "en")
    assert len(env.owner_api.calls) == 2
