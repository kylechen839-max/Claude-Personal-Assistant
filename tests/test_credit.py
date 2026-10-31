"""The beta's starter credit (kwil/credit.py, family.Family.api_for and
UserApi): a beta tester without a key runs on the owner's key until their
credit (or everyone's) is used up, then hears "credit_spent".

All offline: the owner's client is test_userkeys' mocked Anthropic client,
answering with usage in the SDK's shape, so each call's cost is known."""

import json
import logging
import threading
import time

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
        env.fam.api_for(tess_id, credit=True)
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
    # $0.20 left of everyone's: this request's reservation doesn't fit it
    assert env.reply(ugo) == i18n.t("credit_spent", usd="5")
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


def ledger(tmp_path, per_person=1.0, cap=50.0, clock=None, **assistant):
    config = load_config(None, None)
    config["family"].update(dir=str(tmp_path), beta_credit_usd=per_person,
                            beta_credit_total_usd=cap)
    config["assistant"].update(assistant)
    kw = {"monotonic": clock} if clock is not None else {}
    return credit_mod.CreditLedger(config, tmp_path, **kw)


U = "u_abcdefgh1"


def test_the_reserve_is_this_requests_worst_case():
    """request_bound: text one token a byte, an image or PDF page at its
    allowance (never its base64 size), server tool results read again on
    each server tool use, and the whole max_tokens of output."""
    import base64

    pdf = base64.b64encode(b"%PDF-1.4 " + b"x" * 300_000).decode()
    kwargs = {"model": "claude-sonnet-5-5", "max_tokens": 4000, "system": "s" * 1000,
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": "hi"},
                  {"type": "image", "source": {"type": "base64", "data": "A" * 900_000}},
                  {"type": "document", "source": {"type": "base64",
                                                  "media_type": "application/pdf",
                                                  "data": pdf}}]}]}
    tokens_in, tokens_out, searches = credit_mod.request_bound(kwargs)
    assert tokens_out == 4000 and searches == 0
    assert tokens_in >= 1000 + credit_mod.IMAGE_TOKENS + 100 * credit_mod.PAGE_TOKENS
    assert tokens_in < 1000 + credit_mod.IMAGE_TOKENS + 100 * credit_mod.PAGE_TOKENS + 500
    # priced at the dearest input rate (cache write, $2.50) and $10 output
    assert credit_mod.request_reserve(kwargs) == pytest.approx(
        tokens_in * 2.5e-6 + 4000 * 10e-6)
    # web search and fetch: their results, re-read on every use
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3,
              "max_content_tokens": 8000}]
    plain = credit_mod.request_bound({"messages": [], "tools": tools})[0]
    tools_bytes = len(json.dumps(tools).encode()) + len(b'""')  # and the empty system
    assert plain == (tools_bytes + 3 * credit_mod.SEARCH_RESULT_TOKENS + 24_000) * 7
    assert credit_mod.request_bound({"messages": [], "tools": tools})[2] == 3


def test_an_unknown_model_turns_the_credit_off_and_is_never_free(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="kwil.credit"):
        book = ledger(tmp_path, per_person=5, model="claude-opus-9-9")
    assert not book.enabled and book.unpriced == ["claude-opus-9-9"]
    assert "no price for claude-opus-9-9" in caplog.text
    assert book.admit(U, 0.01, wait=0) is None
    assert not ledger(tmp_path, per_person=5, fast_model="claude-new-fast").enabled
    assert ledger(tmp_path, per_person=5, router="off", fast_model="claude-new").enabled
    assert ledger(tmp_path, per_person=5).enabled
    # priced at the dearest known rates: Sonnet's $2.50 input, $10 output
    one = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}
    assert credit_mod.call_cost("claude-opus-9-9", one) == pytest.approx(2.5)
    assert credit_mod.call_cost("claude-opus-9-9", {**one, "input": 0, "output": 1_000_000}) \
        == pytest.approx(10.0)
    assert credit_mod.request_reserve({"model": "claude-opus-9-9", "max_tokens": 1000,
                                       "messages": []}) > 0


@pytest.mark.parametrize("per_person,cap,uids", [(1.0, 50.0, ["u_same00001"] * 8),
                                                 (50.0, 1.0, [f"u_other0000{i}"
                                                              for i in range(8)])])
def test_parallel_admits_never_overshoot(tmp_path, per_person, cap, uids):
    """Everyone's cap: whole reservations must fit. One person's: at most
    MAX_IN_FLIGHT calls at once."""
    book = ledger(tmp_path, per_person, cap)
    reserve = 0.3
    fits = credit_mod.MAX_IN_FLIGHT if per_person < cap else int(cap // reserve)
    barrier = threading.Barrier(len(uids))
    tokens = []

    def go(uid):
        barrier.wait()
        tokens.append(book.admit(uid, reserve, wait=0))

    threads = [threading.Thread(target=go, args=(u,)) for u in uids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([t for t in tokens if t is not None]) == fits
    for uid, token in zip(uids, tokens):  # settled at their worst case
        if token is not None:
            book.settle(uid, reserve, token)
    assert book.total <= cap + 1e-9
    if per_person < cap:  # passed by at most MAX_IN_FLIGHT calls' cost
        assert book.total <= per_person + credit_mod.MAX_IN_FLIGHT * reserve


def test_a_third_call_waits_for_one_to_end(tmp_path):
    book = ledger(tmp_path, per_person=5)
    first, second = book.admit(U, 0.1), book.admit(U, 0.1)
    assert first and second
    assert book.admit(U, 0.1, wait=0) is None
    threading.Timer(0.2, book.settle, args=(U, 0.05, first)).start()
    assert book.admit(U, 0.1, wait=5) is not None


def test_a_reservation_that_expires_is_charged_in_full(tmp_path):
    now = [0.0]
    book = ledger(tmp_path, per_person=5, clock=lambda: now[0])
    token = book.admit(U, 0.4)
    now[0] += book.ttl + 1  # never settled (the call's thread died, say)
    assert book.check(U)
    assert book.used(U) == pytest.approx(0.4) and book.total == pytest.approx(0.4)
    saved = json.loads((tmp_path / credit_mod.FILE).read_text())
    assert saved["people"][U] == pytest.approx(0.4)
    # its call settles after all: the real cost replaces the full charge
    book.settle(U, 0.1, token)
    assert book.used(U) == pytest.approx(0.1) and book.total == pytest.approx(0.1)


def test_left_subtracts_calls_in_flight_and_everyones_remainder(tmp_path):
    book = ledger(tmp_path, per_person=5, cap=6)
    assert book.left(U) == 5
    token = book.admit(U, 1.5)
    assert book.left(U) == pytest.approx(3.5)
    other = book.admit("u_other00001", 3.0)
    assert book.left(U) == pytest.approx(1.5)  # everyone's: 6 - 1.5 - 3
    book.settle(U, 0.5, token)
    book.settle("u_other00001", 3.0, other)
    assert book.left(U) == pytest.approx(2.5)  # min(5 - 0.5, 6 - 3.5)


def test_an_unreadable_ledger_fails_closed(tmp_path, caplog):
    (tmp_path / credit_mod.FILE).write_text("{not json")
    with caplog.at_level(logging.ERROR):
        book = ledger(tmp_path, per_person=5)
    assert book.broken and book.admit(U, 0.01, wait=0) is None
    assert book.left(U) == 0 and "unreadable" in caplog.text


def test_off_means_no_key_as_before(tmp_path):
    for per_person, cap in ((0, 50), (5, 0)):
        book = ledger(tmp_path, per_person, cap)
        assert not book.enabled and book.admit(U, 0.01, wait=0) is None


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


# --- what a call is charged, through the real SDK (an httpx MockTransport) -----------

def sdk_client(handler):
    """A real anthropic.Anthropic whose requests `handler(request, http)`
    answers (no retries, no network)."""
    import anthropic

    for name in ("httpx2", "httpx"):  # newer SDKs are built on httpx2
        try:
            http = __import__(name)
            return anthropic.Anthropic(
                api_key="sk-ant-test-" + "x" * 20, max_retries=0,
                http_client=http.Client(transport=http.MockTransport(
                    lambda request: handler(request, http))))
        except (ImportError, TypeError):
            continue
    raise AssertionError("no HTTP library the SDK accepts")


def sse(model, deltas, complete=True):
    events = [{"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}}]
    events += [{"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": d}} for d in deltas]
    if complete:
        events += [{"type": "content_block_stop", "index": 0},
                   {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                       "stop_sequence": None},
                    "usage": {"output_tokens": 20}},
                   {"type": "message_stop"}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


class Api:
    """The owner's key as a real SDK client; `answer(request_json, http)`
    gives each response."""

    def __init__(self, env, answer):
        self.bodies = []

        def handler(request, http):
            body = json.loads(request.content)
            self.bodies.append(body)
            return answer(body, http, request)

        env.fam._client = sdk_client(handler)


def stream_answer(deltas, complete=True):
    def answer(body, http, request):
        return http.Response(200, headers={"content-type": "text/event-stream"},
                             content=sse(body["model"], deltas, complete).encode())
    return answer


def partial_cost(body):
    """What a cut-short stream is charged: the input its snapshot reports
    and the request's whole max_tokens of output."""
    return credit_mod.call_cost(body["model"], {
        "input": 1000, "output": body["max_tokens"], "cache_read": 0, "cache_write": 0,
        "web_searches": 0})


def chat_of(env, uid):
    from kwil.tools import ToolRegistry

    return env.fam.context(uid).chat_factory(ToolRegistry())


def test_a_whole_stream_is_charged_its_usage(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    api = Api(env, stream_answer(["Hello ", "there."]))
    assert env.reply(tess, "hello") == "Hello there."
    (body,) = api.bodies
    assert env.fam.credit.used(tess_id) == pytest.approx(round(credit_mod.call_cost(
        body["model"], {"input": 1000, "output": 20, "cache_read": 0, "cache_write": 0,
                        "web_searches": 0}), 4))
    assert not env.fam.credit._held


def test_a_stream_stopped_mid_way_is_charged(env, owner):
    """Stop or a barge-in (_Cancelled): Anthropic bills it, so does the credit."""
    tess, tess_id = env.member(owner, "Tess")
    api = Api(env, stream_answer(["Hello ", "there, ", "more ", "words."]))
    cancel = threading.Event()
    chat = chat_of(env, tess_id)
    chat.ask("hello", on_text=lambda delta: cancel.set(), cancel=cancel)
    (body,) = api.bodies
    assert env.fam.credit.used(tess_id) == pytest.approx(round(partial_cost(body), 4))
    assert env.fam.credit.used(tess_id) > 0 and not env.fam.credit._held


def test_a_stream_that_breaks_mid_way_is_charged(env, owner):
    """The connection drops before message_stop (StreamEndedEarly)."""
    tess, tess_id = env.member(owner, "Tess")
    api = Api(env, stream_answer(["Hel"], complete=False))
    said = env.reply(tess, "hello")
    assert said != "Hel"  # the turn failed
    (body,) = api.bodies
    assert env.fam.credit.used(tess_id) == pytest.approx(round(partial_cost(body), 4))
    assert not env.fam.credit._held


def test_a_timeout_with_no_usage_is_charged_its_whole_reservation(env, owner, monkeypatch):
    tess, tess_id = env.member(owner, "Tess")
    reserves = []
    real = credit_mod.request_reserve
    monkeypatch.setattr(credit_mod, "request_reserve",
                        lambda kwargs: reserves.append(real(kwargs)) or reserves[-1])

    def answer(body, http, request):
        raise http.ReadTimeout("no answer", request=request)

    Api(env, answer)
    env.reply(tess, "hello")
    assert len(reserves) == 1
    assert env.fam.credit.used(tess_id) == pytest.approx(round(reserves[0], 4))
    assert not env.fam.credit._held


@pytest.mark.parametrize("failure", ["status", "connect"])
def test_a_call_anthropic_refused_or_never_got_is_free(env, owner, failure):
    tess, tess_id = env.member(owner, "Tess")

    def answer(body, http, request):
        if failure == "connect":
            raise http.ConnectError("no route", request=request)
        return http.Response(529, json={"type": "error", "error": {
            "type": "overloaded_error", "message": "Overloaded"}})

    Api(env, answer)
    env.reply(tess, "hello")
    assert env.fam.credit.used(tess_id) == 0 and not env.fam.credit._held


def test_create_calls_are_reserved_and_settled_too(env, owner):
    """messages.create (history titles, summaries) goes through it as well,
    also after with_options (the Recorder's title call)."""
    from kwil.family import CreditMessages, UserApi

    tess, tess_id = env.member(owner, "Tess")
    api = UserApi(env.fam, tess_id, credit=True).client_or_none()
    assert isinstance(api, UserApi)
    messages = api.with_options(max_retries=1, timeout=20).messages
    assert isinstance(messages, CreditMessages)
    env.owner_api.queue.append(make_response(text_block("Title"), usage=ONE_DOLLAR))
    messages.create(model="claude-haiku-5-5", max_tokens=100,
                    messages=[{"role": "user", "content": "x"}])
    assert env.fam.credit.used(tess_id) == pytest.approx(1.0)
    env.owner_api.messages.create.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        messages.create(model="claude-haiku-5-5", max_tokens=100,
                        messages=[{"role": "user", "content": "x"}])
    assert env.fam.credit.used(tess_id) > 1.0 and not env.fam.credit._held


# --- retries, other paths, and waiting for calls in flight ------------------------------

def message_json(model):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "Hi"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1000, "output_tokens": 20}}


SMALL = {"model": "claude-haiku-5-5", "max_tokens": 100,
         "messages": [{"role": "user", "content": "x"}]}


def done_cost():
    return credit_mod.call_cost("claude-haiku-5-5", {
        "input": 1000, "output": 20, "cache_read": 0, "cache_write": 0, "web_searches": 0})


def retrying_owner(env, answers):
    """The owner's key as the real SDK with 2 retries (as [assistant]
    api_max_retries would give it); `answers` answer the tries in turn."""
    import anthropic

    tries = []

    def handler(request, http):
        tries.append(request)
        return answers[len(tries) - 1](request, http)

    client = sdk_client(handler).with_options(max_retries=2)
    assert isinstance(client, anthropic.Anthropic) and client.max_retries == 2
    env.fam._client = client
    return tries


def test_a_timed_out_try_is_charged_and_retried_by_the_credit(env, owner, monkeypatch):
    """The SDK's own retries would bill each timed-out try and settle only the
    last: the credit makes every try itself, each reserved and settled."""
    from kwil import family as fam

    tess, tess_id = env.member(owner, "Tess")
    slept = []
    monkeypatch.setattr(fam, "_sleep", slept.append)

    def timeout(request, http):
        raise http.ReadTimeout("no answer", request=request)

    tries = retrying_owner(env, [timeout, lambda r, http: http.Response(
        200, json=message_json("claude-haiku-5-5"))])
    api = fam.UserApi(env.fam, tess_id, credit=True)
    assert api.messages.create(**SMALL).content[0].text == "Hi"
    assert len(tries) == 2 and len(slept) == 1 and 0.3 <= slept[0] <= 0.5
    reserve = credit_mod.request_reserve(SMALL)
    assert env.fam.credit.used(tess_id) == pytest.approx(round(reserve + done_cost(), 4))
    assert not env.fam.credit._held


def test_a_429_then_success_charges_only_the_success(env, owner, monkeypatch):
    from kwil import family as fam

    tess, tess_id = env.member(owner, "Tess")
    slept = []
    monkeypatch.setattr(fam, "_sleep", slept.append)
    limited = lambda r, http: http.Response(  # noqa: E731
        429, headers={"retry-after": "2"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
    tries = retrying_owner(env, [limited, lambda r, http: http.Response(
        200, json=message_json("claude-haiku-5-5"))])
    fam.UserApi(env.fam, tess_id, credit=True).messages.create(**SMALL)
    assert len(tries) == 2 and slept == [2.0]  # Anthropic's retry-after
    assert env.fam.credit.used(tess_id) == pytest.approx(round(done_cost(), 4))
    # a 400 isn't tried again (nor charged)
    bad = lambda r, http: http.Response(  # noqa: E731
        400, json={"type": "error", "error": {"type": "invalid_request_error",
                                              "message": "no"}})
    tries = retrying_owner(env, [bad, bad, bad])
    import anthropic

    with pytest.raises(anthropic.BadRequestError):
        fam.UserApi(env.fam, tess_id, credit=True).messages.create(**SMALL)
    assert len(tries) == 1
    assert env.fam.credit.used(tess_id) == pytest.approx(round(done_cost(), 4))


def test_nothing_but_messages_reaches_the_owners_key_on_the_credit(env, owner):
    from kwil.family import UserApi

    tess, tess_id = env.member(owner, "Tess")
    api = UserApi(env.fam, tess_id, credit=True)
    for name in ("beta", "completions", "models"):
        with pytest.raises(AttributeError):
            getattr(api, name)
    messages = api.messages
    for name in ("batches", "with_raw_response", "with_streaming_response"):
        with pytest.raises(AttributeError):
            getattr(messages, name)
    assert messages.count_tokens is env.owner_api.messages.count_tokens  # free
    assert api.with_options(timeout=5).messages is not None
    # a bare client never comes from the credit: MissingKey, not the owner's
    with pytest.raises(userkeys.MissingKey) as e:
        env.fam.client(tess_id)
    assert not isinstance(e.value, userkeys.CreditSpent)
    with pytest.raises(userkeys.MissingKey):
        env.fam.api_for(tess_id)
    with pytest.raises(userkeys.MissingKey):
        UserApi(env.fam, tess_id).messages
    assert env.owner_api.calls == [] and env.fam.credit.total == 0
    # a family member on the owner's key still gets the whole client
    mom, mom_id = env.member(owner, "Mom", tier="family")
    assert UserApi(env.fam, mom_id).beta is env.owner_api.beta


def test_a_title_overlapping_a_reply_waits_instead_of_being_refused(tmp_path):
    """$1.50 left and a reply's $1.80 reservation in flight: the title call
    waits for the reply to settle, then runs; it is never "used up"."""
    book = ledger(tmp_path, per_person=5)
    book.settle(U, 3.5, book.admit(U, 0.1))  # $3.50 of their $5 used
    reply = book.admit(U, 1.8)
    assert reply is not None
    assert book.check(U)  # what's spent still leaves $1.50
    threading.Timer(0.2, book.settle, args=(U, 0.03, reply)).start()
    started = time.monotonic()
    title = book.admit(U, 0.46, wait=5)
    assert title is not None and 0.15 < time.monotonic() - started < 4
    # money actually used up: refused at once, no waiting
    book.settle(U, 1.5, title)
    started = time.monotonic()
    assert not book.check(U) and book.admit(U, 0.01, wait=5) is None
    assert time.monotonic() - started < 1


def test_a_beta_testers_history_title_is_on_the_credit(env, owner):
    """The Recorder's title call (chat_factory's title_api) is reserved and
    settled on the starter credit, not refused or left uncounted."""
    from kwil.tools import ToolRegistry

    tess, tess_id = env.member(owner, "Tess")
    chat = env.fam.context(tess_id).chat_factory(ToolRegistry())
    answer(env, 1, make_usage(1000, 100))  # the reply
    chat.ask("hi there")
    before, calls = env.fam.credit.used(tess_id), len(env.owner_api.calls)
    env.owner_api.queue.append(make_response(
        text_block('{"title": "Greeting", "summary": "Said hi."}'), usage=ONE_DOLLAR))
    chat.end_conversation()
    chat.recorder.flush()
    from kwil.history import TITLE_PROMPT

    assert any(c.get("system") == TITLE_PROMPT for c in env.owner_api.calls[calls:])
    assert env.fam.credit.used(tess_id) == pytest.approx(before + 1.0, abs=1e-3)
    assert not env.fam.credit._held
