"""The Chinese instance ([assistant] language = "zh"): strict Chinese yes/no,
the assistant's own sentences, and confirmation questions. Offline."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from assistant import cli, i18n
from assistant.voice_session import decline_message, is_explicit_yes, question_suffix
from tests.test_voice import SPEECH, delete_tool, make_conv, user_texts
from tests.voice_fakes import stream_client, streamed
from tests.conftest import tool_use_block


@pytest.fixture
def zh(monkeypatch):
    monkeypatch.setattr(i18n, "LANG", "zh")


@pytest.mark.parametrize("answer, yes", [
    ("好的", True), ("是的。", True), ("对", True), ("可以", True), ("行", True),
    ("确定", True), ("没问题", True), ("好，删吧", True),
    ("不好", False), ("不要", False), ("好不好", False), ("没有", False),
    ("可以吗？", False), ("是吗", False), ("算了", False), ("等等", False),
    ("别删", False), ("取消", False), ("", False),
    # English is unchanged
    ("yes", True), ("go ahead", True), ("no", False), ("yes but not that one", False),
])
def test_is_yes_chinese(answer, yes):
    assert cli.is_yes(answer) is yes


@pytest.mark.parametrize("answer, yes", [
    ("好的", True), ("是的。", True), ("没问题", True), ("好，删吧", True),
    ("不好", False), ("不要", False), ("好不好", False), ("没有", False),
    ("可以吗？", False), ("好的呢", False),
    ("好的，那明天下午的会议呢", False),  # more than a short yes
    ("好的我们明天再说吧", False),
    ("yes", True), ("Yeah, delete it.", True), ("okay, and what about Friday?", False),
])
def test_explicit_yes_chinese(answer, yes):
    assert is_explicit_yes(answer) is yes


@pytest.mark.parametrize("text, leaves", [
    ("再见", True), ("拜拜！", True), ("再见。", True), ("goodbye", True),
    ("再见到他了吗", False), ("明天见", False)])
def test_exit_words(text, leaves):
    assert cli.is_exit(text) is leaves


def test_set_language():
    assert i18n.set_language("zh-CN") == "zh" and i18n.is_zh()
    assert i18n.t("goodbye") == "再见！"
    assert i18n.set_language("en") == "en" and i18n.t("goodbye") == "Goodbye!"
    assert i18n.set_language(None) == "en"
    assert set(i18n.ZH) == set(i18n.EN)  # every sentence has its Chinese


def test_assistant_sentences_in_chinese(zh, fake_client):
    assert question_suffix() == "请说“是”或者“不”。"
    assert decline_message("要我删除这个日程吗？") == "好的，我不删除了。"
    assert decline_message("要我改这个日程的时间吗？") == "好的，我不做了。"
    assert cli.friendly_error(RuntimeError("x"), "hi", lambda m: None) == "抱歉，出了点问题。"


def test_english_sentences_unchanged():
    assert question_suffix() == " Please say yes or no."
    assert decline_message("Do you want me to delete X?") == "OK, I won't delete it."


def test_spoken_datetime():
    assert i18n.spoken_datetime(datetime(2026, 10, 21, 15, 30)) == "10月21日下午3点30分"
    assert i18n.spoken_datetime(datetime(2026, 10, 21, 9, 0)) == "10月21日上午9点"
    assert i18n.spoken_datetime(datetime(2026, 1, 2, 20, 5)) == "1月2日晚上8点5分"


def test_calendar_questions_in_chinese(zh):
    from assistant.tools.calendar import GoogleCalendar

    service = MagicMock()
    service.events.return_value.get.return_value.execute.return_value = {
        "id": "ev1", "summary": "看牙医", "organizer": {"self": True},
        "start": {"dateTime": "2026-10-17T15:00:00-04:00"},
        "end": {"dateTime": "2026-10-17T16:00:00-04:00"}}
    cal = GoogleCalendar(service=service, timezone="America/New_York")
    assert cal.describe_for_delete({"event_id": "ev1"}) == "要我删除10月17日下午3点的“看牙医”吗？"
    assert cal.describe_for_create({"title": "买菜", "start": "2026-10-20T09:30:00"}) == \
        "要我把“买菜”加到10月20日上午9点30分的日历上吗？"
    assert cal.describe_for_move({"event_id": "ev1", "new_start": "2026-10-18T14:00:00"}) == \
        "要我把“看牙医”改到10月18日下午2点吗？"
    service.events.return_value.get.return_value.execute.side_effect = RuntimeError
    assert cal.describe_for_delete({"event_id": "x"}) == "要我删除这个日程吗？"


def test_gmail_draft_question_in_chinese(zh):
    from tests.test_gmail import inbox

    gmail, _, _ = inbox()
    q = gmail.describe_for_draft({"message_id": "m1", "body": "谢谢"})
    assert q == "要我给Prof. Smith at smith@uni.edu存一封回复草稿吗？内容是：谢谢。不会发送出去。"


def test_voice_confirmation_in_chinese(zh):
    """A spoken "好的" confirms; "不要" declines, in Chinese."""
    for answer, yes in (("好的", True), ("不要", False)):
        deleted = []
        client = stream_client(
            streamed("好。", extra=[tool_use_block("delete_event", {"title": "看牙医"})],
                     stop_reason="tool_use"),
            streamed("好了。"))
        conv, chat, events = make_conv(client, "删掉看牙医", answer,
                                       tools=delete_tool(deleted))
        conv.utterance(1, SPEECH)
        asked = events.wait_for("audio", question=True)
        assert asked["text"].endswith("请说“是”或者“不”。")
        conv.chunk_played(1, asked["index"])
        conv.utterance(2, SPEECH)
        assert events.wait_for("confirm_answer")["yes"] is yes
        events.wait_for("turn_done", turn=1)
        assert deleted == (["看牙医"] if yes else [])
        assert answer not in user_texts(client)


def test_chinese_no_answer_status_clears_the_question(zh):
    from assistant.voice_session import PendingConfirm

    conv, _, events = make_conv(stream_client())
    conv.pending = PendingConfirm(1, "要我删除吗？")
    conv.on_status(i18n.t("no_answer"))
    assert conv.pending is None


def test_cjk_question_in_english_mode_is_still_not_a_yes():
    assert not is_explicit_yes("可以吗？") and not cli.is_yes("可以吗？")
