"""The few fixed sentences the assistant says itself, in English or Chinese.

`[assistant] language` picks the language: "en" (the default) or "zh"
(Simplified Chinese, Mandarin). `set_language()` is called once at startup
(cli.main, webapp.serve); `t(key, **values)` gives the sentence. Claude's
own replies follow the system prompt (chat.SYSTEM_PROMPT_ZH), not this table.
"""

from __future__ import annotations

LANG = "en"

EN = {
    "goodbye": "Goodbye!",
    "error": "Sorry, something went wrong.",
    "no_internet": "Sorry, I can't reach the internet right now.",
    "bad_key": "Sorry, my Anthropic API key isn't working. Please check it.",
    "rate_limited": "I'm getting too many requests right now. Try again in a minute.",
    "api_error": "Sorry, something went wrong talking to Claude.",
    "say_yes_no": " Please say yes or no.",
    "no_answer": "No answer, so I didn't do it.",
    "not_caught": "I didn't catch that. Try again?",
    "wont_delete": "OK, I won't delete it.",
    "wont_do": "OK, I won't do that.",
    "cal_delete": "Do you want me to delete {title} on {when}?",
    "cal_delete_any": "Do you want me to delete that event?",
    "cal_create": "Do you want me to add {title} on {when} to your calendar?",
    "cal_create_any": "Do you want me to add that event to your calendar?",
    "cal_move": "Do you want me to move {title} to {when}?",
    "cal_move_any": "Do you want me to move that event?",
    "draft": "Do you want me to save a draft reply to {to}, saying: {text}? "
             "It won't be sent.{warning}",
    "draft_also": " Careful: replies also go to {others}.",
    "draft_instead": " Careful: replies go to {others}, not to the sender {sender}.",
    "google_failed": "Google sign-in didn't finish. Go back to the app and try again.",
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
}

ZH = {
    "goodbye": "再见！",
    "error": "抱歉，出了点问题。",
    "no_internet": "抱歉，现在连不上网络。",
    "bad_key": "抱歉，Anthropic 密钥不能用了，请检查一下。",
    "rate_limited": "现在请求太多了，请过一分钟再试。",
    "api_error": "抱歉，连接 Claude 时出了点问题。",
    "say_yes_no": "请说“是”或者“不”。",
    "no_answer": "没有听到回答，所以我没有做。",
    "not_caught": "我没听清楚，请再说一遍？",
    "wont_delete": "好的，我不删除了。",
    "wont_do": "好的，我不做了。",
    "cal_delete": "要我删除{when}的“{title}”吗？",
    "cal_delete_any": "要我删除这个日程吗？",
    "cal_create": "要我把“{title}”加到{when}的日历上吗？",
    "cal_create_any": "要我把这个日程加到日历上吗？",
    "cal_move": "要我把“{title}”改到{when}吗？",
    "cal_move_any": "要我改这个日程的时间吗？",
    "draft": "要我给{to}存一封回复草稿吗？内容是：{text}。不会发送出去。{warning}",
    "draft_also": "注意：回复也会发给 {others}。",
    "draft_instead": "注意：回复会发给 {others}，而不是发件人 {sender}。",
    "google_failed": "Google 登录没有完成。请回到应用里再试一次。",
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
}


def set_language(language: str | None) -> str:
    """"zh..." -> Chinese, anything else -> English. Returns the code used."""
    global LANG
    LANG = "zh" if str(language or "").lower().startswith("zh") else "en"
    return LANG


def is_zh() -> bool:
    return LANG == "zh"


def t(key: str, **values) -> str:
    table = ZH if LANG == "zh" else EN
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
