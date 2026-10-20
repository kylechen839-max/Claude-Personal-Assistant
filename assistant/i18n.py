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
    "file_too_long": "That file is too long for Claude (PDFs up to 100 pages).",
    "file_unreadable": "Claude couldn't read that file.",
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
}

ZH = {
    "goodbye": "再见！",
    "error": "抱歉，出了点问题。",
    "no_internet": "抱歉，现在连不上网络。",
    "bad_key": "抱歉，Anthropic 密钥不能用了，请检查一下。",
    "rate_limited": "现在请求太多了，请过一分钟再试。",
    "api_error": "抱歉，连接 Claude 时出了点问题。",
    "file_too_long": "这个文件太长了，Claude 处理不了（PDF 最多 100 页）。",
    "file_unreadable": "Claude 读不了这个文件。",
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
