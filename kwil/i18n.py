"""The few fixed sentences the assistant says itself, in English or Chinese.

`[assistant] language` picks the language: "en" (the default) or "zh"
(Simplified Chinese, Mandarin). `set_language()` is called once at startup
(cli.main, webapp.serve); `t(key, **values)` gives the sentence. Claude's
own replies follow the system prompt (chat.SYSTEM_PROMPT_ZH), not this table.

On a shared server (family members, family.py) each person has their own
language: `t(key, lang)` takes it explicitly, and `using(lang)` sets it for
everything a turn's thread calls (a tool's confirmation question, an error
sentence), so one person's turn never speaks another's language. Threads
don't inherit it: set it at the start of each turn's thread.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

LANG = "en"
# this thread's (turn's) language, over LANG; None = LANG
_SESSION: ContextVar[str | None] = ContextVar("i18n_language", default=None)

EN = {
    "goodbye": "Goodbye!",
    "error": "Sorry, something went wrong.",
    "no_internet": "Sorry, I can't reach the internet right now.",
    "bad_key": "Sorry, my Anthropic API key isn't working. Please check it.",
    "no_key": "Add your Anthropic API key in Settings to start.",
    "bad_user_key": "Your Anthropic API key was rejected. Check it in Settings.",
    "rate_limited": "I'm getting too many requests right now. Try again in a minute.",
    "api_error": "Sorry, something went wrong talking to Claude.",
    "claude_down": "Claude is unreachable right now. I retried a few times; "
                   "please try again in a minute.",
    "file_too_long": "That file is too long for Claude (PDFs up to 100 pages).",
    "file_unreadable": "Claude couldn't read that file.",
    "say_yes_no": " Please say yes or no.",
    "no_answer": "No answer, so I didn't do it.",
    # an unclear answer to a yes/no question: asked once more, then dropped
    "confirm_again": "Sorry, I didn't catch that.",
    "confirm_unclear": "I still didn't catch that, so I left it.",
    "not_caught": "I didn't catch that. Try again?",
    # a turn typed into a conversation deleted meanwhile (another phone)
    "conversation_gone": "That conversation was deleted, so I didn't send this.",
    "wont_delete": "OK, I won't delete it.",
    "wont_do": "OK, I won't do that.",
    "cal_delete": "Do you want me to delete {title} on {when}?",
    "cal_delete_any": "Do you want me to delete that event?",
    "cal_create": "Do you want me to add {title} on {when} to your calendar?",
    "cal_create_any": "Do you want me to add that event to your calendar?",
    "cal_move": "Do you want me to move {title} to {when}?",
    "cal_move_any": "Do you want me to move that event?",
    "cal_delete_series": "Do you want me to delete every {title}, the whole series?",
    "cal_delete_series_any": "Do you want me to delete every event in that series?",
    "cal_update": "Do you want me to change {title}?",
    "cal_update_any": "Do you want me to change that event?",
    "cal_invite": "Do you want me to send an invite for {title} to {emails}?",
    "cal_invite_any": "Do you want me to send that invite?",
    "cal_uninvite": "Do you want me to remove {emails} from {title}?",
    "cal_uninvite_any": "Do you want me to change who's invited to that event?",
    "cal_rsvp": "Do you want me to {response} {title} from {organizer}?",
    "cal_rsvp_any": "Do you want me to answer that invite?",
    "cal_rsvp_note": "Do you want me to {response} {title} from {organizer}, "
                     "and send the note: {comment}?",
    "cal_rsvp_note_any": "Do you want me to answer that invite and send the "
                         "note: {comment}?",
    "cal_that_event": "that event",
    "rsvp_accepted": "accept",
    "rsvp_declined": "decline",
    "rsvp_tentative": "say maybe to",
    "draft": "Do you want me to save a draft reply to {to}, saying: {text}? "
             "It won't be sent.{warning}",
    "draft_also": " Careful: replies also go to {others}.",
    "draft_instead": " Careful: replies go to {others}, not to the sender {sender}.",
    "google_failed": "Google sign-in didn't finish. Go back to the app and try again.",
    "notion_failed": "Notion sign-in didn't finish. Go back to the app and try again.",
    "connect_failed": "The sign-in didn't finish. Go back to the app and try again.",
    "notion_reconnect": "The Notion sign-in stopped working. Reconnect Notion from the "
                        "app's settings: tap the gear, then \"Connect Notion\".",
    "google_beta_off": "Google isn't set up for beta accounts yet.",
    "note_delete": "Do you want me to delete the note: {text}?",
    "note_delete_any": "Do you want me to delete that note?",
    # a student's deadlines (tools/deadlines.py)
    "deadline_confirm": "Do you want me to mark {title} as due {when}?",
    "deadline_add": "Do you want me to add {title}, due {when}, to your deadlines?",
    "deadline_delete": "Do you want me to delete the deadline {title}, due {when}?",
    "deadline_delete_any": "Do you want me to delete that deadline?",
    # Home Assistant (tools/home_assistant.py)
    "ha_ask_unlock": "Do you want me to unlock {name}?",
    "ha_ask_lock": "Do you want me to lock {name}?",
    "ha_ask_open": "Do you want me to open {name}?",
    "ha_ask_close": "Do you want me to close {name}?",
    "ha_ask_disarm": "Do you want me to disarm {name}?",
    "ha_ask_arm": "Do you want me to arm {name}?",
    "ha_ask_on": "Do you want me to turn on {name}?",
    "ha_ask_off": "Do you want me to turn off {name}?",
    "ha_ask_change": "Do you want me to {action} {name}?",
    "ha_and_more": " and {n} more",
    "ha_not_set_up": "Home Assistant isn't set up yet: HA_URL and HA_TOKEN are missing.",
    "ha_down": "I can't reach Home Assistant right now. Is it running?",
    "ha_auth": "Home Assistant didn't accept my access token. "
               "It needs a new long-lived token.",
    "ha_reconnect": "The Home Assistant sign-in stopped working. Connect it again "
                    "in the app's settings: tap the gear, then Home Assistant "
                    "under Connections.",
    "ha_not_found": "Home Assistant doesn't know that device.",
    "ha_rejected": "Home Assistant couldn't do that.",
    "ha_no_match": "I couldn't find a device called {name}.",
    "ha_which": "Which one do you mean: {names}?",
    "ha_domain_off": "I'm not allowed to control {domain} devices.",
    "ha_read_only": "That's a sensor, so I can only read it.",
    "ha_unsupported": "I can't {action} a {domain} device.",
    "ha_one_at_a_time": "Locks, garage doors and alarms go one at a time, by name.",
    "ha_refused_untrusted": "I won't unlock or open anything while web or email "
                            "content is in this conversation. Ask me again in a "
                            "new conversation.",
    "ha_needs_yes": "That needs your spoken yes first, so I didn't do it.",
    "ha_ask_again": "Home Assistant didn't answer while I was asking, so I "
                    "didn't do it. Please ask me again.",
    # team chat digests (tools/teamchat.py)
    "chat_digest": "Do you want me to save the digest {title} to Notion?",
    "chat_digest_any": "Do you want me to save that digest to Notion?",
    "spotify_failed": "Spotify sign-in didn't finish. Go back to the app and try again.",
    "spotify_premium": "Spotify only lets apps control playback with a Premium account.",
    "spotify_no_device": "Spotify isn't open anywhere. Open Spotify on your PC or "
                         "phone, then ask again.",
    "spotify_which_device": "Spotify is open on {devices}. Which one should I play on?",
    "spotify_unknown_device": "I can't find a Spotify device called {device}.",
    "spotify_no_volume": "That device doesn't let me change its volume.",
    "spotify_signin": "The Spotify sign-in has expired. Connect Spotify again.",
    "spotify_busy": "Spotify is busy right now. Try again in a moment.",
    "spotify_unreachable": "I can't reach Spotify right now.",
    "spotify_error": "Spotify couldn't do that right now.",
    "spotify_not_found": "I couldn't find {query} on Spotify.",
    "spotify_nothing_playing": "Nothing is playing on Spotify right now.",
    "still_working": "I'm still working on your last request.",
    # an invited person's limits (quotas.py)
    "limit_minute": "That's a lot of messages at once. Please wait a minute and try again.",
    "limit_day": "You've reached today's limit of {per_day} messages. "
                 "You can talk to me again tomorrow.",
    "limit_speech": "You've used today's {minutes} minutes of speech. "
                    "You can still type, and talking works again tomorrow.",
    "google_reconnect": "Reconnect Google from the app's settings: tap the gear, "
                        "then \"Connect Google account\".",
}

ZH = {
    "goodbye": "再见！",
    "error": "抱歉，出了点问题。",
    "no_internet": "抱歉，现在连不上网络。",
    "bad_key": "抱歉，Anthropic 密钥不能用了，请检查一下。",
    "no_key": "请先在设置里添加你的 Anthropic API 密钥。",
    "bad_user_key": "你的 Anthropic API 密钥被拒绝了，请到设置里检查一下。",
    "rate_limited": "现在请求太多了，请过一分钟再试。",
    "api_error": "抱歉，连接 Claude 时出了点问题。",
    "claude_down": "现在连不上 Claude，已经重试了几次，请过一分钟再试。",
    "file_too_long": "这个文件太长了，Claude 处理不了（PDF 最多 100 页）。",
    "file_unreadable": "Claude 读不了这个文件。",
    "say_yes_no": "请说“是”或者“不”。",
    "no_answer": "没有听到回答，所以我没有做。",
    "confirm_again": "不好意思，我没听清。",
    "confirm_unclear": "还是没听清，所以我没有做。",
    "not_caught": "我没听清楚，请再说一遍？",
    "conversation_gone": "那个对话已经删除了，所以这条消息没有发送。",
    "wont_delete": "好的，我不删除了。",
    "wont_do": "好的，我不做了。",
    "cal_delete": "要我删除{when}的“{title}”吗？",
    "cal_delete_any": "要我删除这个日程吗？",
    "cal_create": "要我把“{title}”加到{when}的日历上吗？",
    "cal_create_any": "要我把这个日程加到日历上吗？",
    "cal_move": "要我把“{title}”改到{when}吗？",
    "cal_move_any": "要我改这个日程的时间吗？",
    "cal_delete_series": "要我删除所有的“{title}”，整个系列吗？",
    "cal_delete_series_any": "要我删除这个系列的所有日程吗？",
    "cal_update": "要我修改“{title}”吗？",
    "cal_update_any": "要我修改这个日程吗？",
    "cal_invite": "要我把“{title}”的邀请发给{emails}吗？",
    "cal_invite_any": "要我发出这个邀请吗？",
    "cal_uninvite": "要我把{emails}从“{title}”的邀请里移除吗？",
    "cal_uninvite_any": "要我修改这个日程的邀请对象吗？",
    "cal_rsvp": "要我{response}{organizer}发来的“{title}”吗？",
    "cal_rsvp_any": "要我回复这个邀请吗？",
    "cal_rsvp_note": "要我{response}{organizer}发来的“{title}”，并附上留言：{comment}吗？",
    "cal_rsvp_note_any": "要我回复这个邀请，并附上留言：{comment}吗？",
    "cal_that_event": "这个日程",
    "rsvp_accepted": "接受",
    "rsvp_declined": "拒绝",
    "rsvp_tentative": "暂定参加",
    "draft": "要我给{to}存一封回复草稿吗？内容是：{text}。不会发送出去。{warning}",
    "draft_also": "注意：回复也会发给 {others}。",
    "draft_instead": "注意：回复会发给 {others}，而不是发件人 {sender}。",
    "google_failed": "Google 登录没有完成。请回到应用里再试一次。",
    "notion_failed": "Notion 登录没有完成。请回到应用里再试一次。",
    "connect_failed": "登录没有完成。请回到应用里再试一次。",
    "notion_reconnect": "Notion 连接已失效。请在应用的设置里重新连接 Notion：点齿轮，再点“连接 Notion”。",
    "google_beta_off": "测试版账户暂时还不能连接 Google。",
    "note_delete": "要我删除这条记忆吗：{text}？",
    "note_delete_any": "要我删除这条记忆吗？",
    "deadline_confirm": "要我把{title}的截止时间定为{when}吗？",
    "deadline_add": "要我把{title}加到你的截止日期里吗？截止时间是{when}。",
    "deadline_delete": "要我删除截止日期{title}吗？它的截止时间是{when}。",
    "deadline_delete_any": "要我删除这个截止日期吗？",
    "ha_ask_unlock": "要我把{name}解锁吗？",
    "ha_ask_lock": "要我把{name}锁上吗？",
    "ha_ask_open": "要我打开{name}吗？",
    "ha_ask_close": "要我关上{name}吗？",
    "ha_ask_disarm": "要我解除{name}的警戒吗？",
    "ha_ask_arm": "要我启动{name}的警戒吗？",
    "ha_ask_on": "要我打开{name}吗？",
    "ha_ask_off": "要我关掉{name}吗？",
    "ha_ask_change": "要我对{name}执行“{action}”吗？",
    "ha_and_more": "等{n}个设备",
    "ha_not_set_up": "Home Assistant 还没有设置好：缺少 HA_URL 和 HA_TOKEN。",
    "ha_down": "现在连不上 Home Assistant，它在运行吗？",
    "ha_auth": "Home Assistant 不接受我的访问令牌，需要一个新的长期令牌。",
    "ha_reconnect": "Home Assistant 的登录已失效。请在应用的设置里重新连接：点齿轮，再在“服务连接”里点“Home Assistant”。",
    "ha_not_found": "Home Assistant 里没有这个设备。",
    "ha_rejected": "Home Assistant 没能完成这个操作。",
    "ha_no_match": "我找不到叫“{name}”的设备。",
    "ha_which": "你说的是哪一个：{names}？",
    "ha_domain_off": "我不能控制 {domain} 类的设备。",
    "ha_read_only": "那是传感器，我只能读取它。",
    "ha_unsupported": "{domain} 设备不能执行“{action}”。",
    "ha_one_at_a_time": "门锁、车库门和警报器要一个一个按名字来操作。",
    "ha_refused_untrusted": "对话里有网页或邮件内容时，我不会开锁或开门。请在新的对话里再说一次。",
    "ha_needs_yes": "这需要你先口头说“是”，所以我没有做。",
    "ha_ask_again": "刚才问你的时候 Home Assistant 没有回应，所以我没有做。请再说一次。",
    "chat_digest": "要我把摘要“{title}”存到 Notion 吗？",
    "chat_digest_any": "要我把这份摘要存到 Notion 吗？",
    "spotify_failed": "Spotify 登录没有完成。请回到应用里再试一次。",
    "spotify_premium": "Spotify 只允许高级会员（Premium）账户用应用控制播放。",
    "spotify_no_device": "哪里都没有打开 Spotify。请先在电脑或手机上打开 Spotify，再说一次。",
    "spotify_which_device": "Spotify 在{devices}上开着。要在哪一个上播放？",
    "spotify_unknown_device": "没有找到叫“{device}”的 Spotify 设备。",
    "spotify_no_volume": "这个设备不允许我调音量。",
    "spotify_signin": "Spotify 登录已过期，请重新连接 Spotify。",
    "spotify_busy": "Spotify 现在有点忙，请稍后再试。",
    "spotify_unreachable": "现在连不上 Spotify。",
    "spotify_error": "Spotify 现在做不了这个。",
    "spotify_not_found": "在 Spotify 上没找到“{query}”。",
    "spotify_nothing_playing": "Spotify 现在没有在播放。",
    "still_working": "我还在处理你上一个请求。",
    "limit_minute": "一下子消息太多了，请等一分钟再试。",
    "limit_day": "你今天的 {per_day} 条消息已经用完了，明天再来找我吧。",
    "limit_speech": "你今天的 {minutes} 分钟语音已经用完了。还可以打字，明天就能再说话了。",
    "google_reconnect": "请在应用的设置里重新连接 Google：点齿轮，再点“连接 Google 账户”。",
}


def normalize(language: str | None) -> str:
    """"zh..." -> "zh", anything else -> "en"."""
    return "zh" if str(language or "").lower().startswith("zh") else "en"


def set_language(language: str | None) -> str:
    """The server-wide language. Returns the code used."""
    global LANG
    LANG = normalize(language)
    return LANG


def current() -> str:
    """This turn's language (`using`), else the server's."""
    return _SESSION.get() or LANG


@contextmanager
def using(language: str | None):
    """Speak `language` in this thread (and what it calls) until the block
    ends; None keeps the server's."""
    token = _SESSION.set(normalize(language) if language else None)
    try:
        yield
    finally:
        _SESSION.reset(token)


def is_zh(lang: str | None = None) -> bool:
    return (normalize(lang) if lang else current()) == "zh"


def t(key: str, lang: str | None = None, **values) -> str:
    """The sentence `key` in `lang` (default: `current()`)."""
    table = ZH if is_zh(lang) else EN
    text = table.get(key, EN[key])
    return text.format(**values) if values else text


def spoken_datetime(dt) -> str:
    """'10月21日下午3点30分' style (Chinese) for a confirmation question."""
    hour = dt.hour
    period = ("凌晨" if hour < 6 else "上午" if hour < 12 else
              "中午" if hour < 13 else "下午" if hour < 18 else "晚上")
    h12 = hour % 12 or 12
    minutes = f"{dt.minute}分" if dt.minute else ""
    return f"{dt.month}月{dt.day}日{period}{h12}点{minutes}"
