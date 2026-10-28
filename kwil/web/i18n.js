// Chinese phone page: when the server sends <html lang="zh-CN"> ([assistant]
// language = "zh"), translate the page's fixed English text (text, placeholder,
// aria-label, title) at load and whenever app.js / voice.js change it, and the
// questions passed to confirm(). Only exact known strings are replaced, so
// messages and replies are left as they are. With lang="en" it does nothing.
// Also: the "Connect Google account" menu button ([webapp] google_connect,
// or a family member's own account: the server marks <html data-google>),
// shown only when /api/google/status says it is set up, and "Disconnect
// Google account" when the status allows it (family members). When Google
// refused the saved sign-in (needs_reconnect) the row says it expired and
// the button reconnects.
"use strict";

(function (root) {
  const ZH = {
    "Enter your access token (": "请输入访问令牌（",
    "in the PC's": "，在电脑的",
    "). You only need to do this once on this phone.": "里）。这部手机只需要输入一次。",
    "Access token": "访问令牌",
    "Sign in": "登录",
    "What is Kwil?": "Kwil 是什么？",
    "Incognito": "无痕",
    "Backend in use": "正在使用的服务器",
    "Conversation history": "对话记录",
    "Settings": "设置",
    "Use the phone's voice": "使用手机自带的声音",
    "Don't speak replies": "不朗读回复",
    "Incognito (don't save)": "无痕模式（不保存）",
    "Sign out": "退出登录",
    "Sign out everywhere": "在所有设备上退出",
    "Yes": "是",
    "No": "不",
    'Or hold the mic and say "yes".': "或者按住麦克风说“是”。",
    "Type a message": "输入消息",
    'Type "yes" or "no"': "输入“是”或“不”",
    "Send": "发送",
    "Hold to talk, or tap to start and stop": "按住说话，或点一下开始、再点一下结束",
    "Listening… release or tap to send": "正在听…松开或点一下发送",
    "Voice conversation": "语音对话",
    "Connecting…": "正在连接…",
    "Voice orb (tap to resume when paused)": "语音球（暂停时点一下继续）",
    "Tap to continue": "点一下继续",
    "Mute": "静音",
    "Unmute": "取消静音",
    "End": "结束",
    "Captions": "字幕",
    "Latency timings": "延迟时间",
    "Listening": "正在听",
    "Still listening…": "还在听…",
    "Thinking…": "正在思考…",
    "Speaking": "正在说话",
    "Paused": "已暂停",
    "Ended": "已结束",
    "Loading…": "正在加载…",
    "Connection closed.": "连接已断开。",
    "Paused. Tap to continue.": "已暂停。点一下继续。",
    "The microphone stopped.": "麦克风停止了。",
    "Paused while the app was in the background.": "应用在后台时已暂停。",
    "Ended after a quiet stretch.": "安静了一段时间，已结束。",
    "Paused: the microphone was interrupted.": "已暂停：麦克风被打断了。",
    "Paused: audio was interrupted. Tap to continue.": "已暂停：声音被打断了。点一下继续。",
    "(nothing heard)": "（没有听到）",
    "Conversation ended.": "对话已结束。",
    "Listening back…": "正在识别…",
    "Too short. Try again.": "太短了，请再试一次。",
    "Please sign in.": "请先登录。",
    "Sign-in failed.": "登录失败。",
    "sign-in failed": "登录失败。",
    "too many attempts; try again later": "尝试次数太多，请稍后再试。",
    "still working on the last request": "还在处理上一个请求。",
    // typing while Kwil is thinking (app.js): queued messages and Stop
    "Queued": "排队中",
    "Remove queued message": "移除排队的消息",
    "Stop": "停止",
    "Stopped.": "已停止。",
    "You can queue up to 3 messages.": "最多可以排队 3 条消息。",
    "Sign out on every phone and browser?": "要在所有手机和浏览器上退出登录吗？",
    "This browser can't record. It needs HTTPS (see the README's Tailscale steps).":
      "这个浏览器不能录音（需要 HTTPS 地址）。",
    ["Microphone access is blocked. On iPhone: Settings → Safari → Microphone → " +
      "Allow (or in Safari, aA → Website Settings → Microphone), then reopen the app. " +
      "On Android: tap the lock icon by the address → Permissions → Microphone."]:
      "麦克风权限被关闭了。iPhone：设置 → Safari → 麦克风 → 允许，然后重新打开应用。" +
      "安卓：点地址栏旁边的锁图标 → 权限 → 麦克风。",
    "History": "历史记录",
    "Back to chat": "返回聊天",
    "Search conversations": "搜索对话",
    "Load more": "加载更多",
    "Continue this conversation": "继续这个对话",
    "Cancel": "取消",
    "Save": "保存",
    "Delete": "删除",
    "Connect Google account": "连接 Google 账户",
    "Reconnect Google account": "重新连接 Google 账户",
    "Google account connected.": "Google 账户已连接。",
    // Google refused the saved sign-in (google_web.py needs_reconnect)
    "Google sign-in expired. Reconnect to keep using it.": "Google 登录已过期。请重新连接后继续使用。",
    "Google sign-in expired.": "Google 登录已过期。",
    "Opening Google…": "正在打开 Google…",
    "Couldn't start the Google sign-in.": "无法开始 Google 登录。",
    "Connect Spotify": "连接 Spotify",
    "Reconnect Spotify": "重新连接 Spotify",
    "Spotify connected.": "Spotify 已连接。",
    "Opening Spotify…": "正在打开 Spotify…",
    "Couldn't start the Spotify sign-in.": "无法开始 Spotify 登录。",
    // an invited person's own Notion (notion.js, notion_web.py)
    "Connect Notion": "连接 Notion",
    "Reconnect Notion": "重新连接 Notion",
    "Disconnect Notion": "断开 Notion",
    "Notion connected.": "Notion 已连接。",
    "Notion is connected, but I couldn't find exactly one Assignments database. Tap \"Reconnect Notion\" and share only your Assignments database.":
      "Notion 已连接，但我没有找到唯一的作业（Assignments）数据库。请点“重新连接 Notion”，只共享你的作业数据库。",
    "Opening Notion…": "正在打开 Notion…",
    "Couldn't start the Notion sign-in.": "无法开始 Notion 登录。",
    "Disconnect Notion? I won't see your assignments any more.":
      "要断开 Notion 吗？之后我就看不到你的作业了。",
    "Notion disconnected.": "Notion 已断开。",
    // the menu's Connections rows and sheets (connections.js, index.html):
    // headings, subtitles, status words, action words and explanations
    "Connections": "服务连接",
    "Connected": "已连接",
    "Available": "可连接",
    "Not connected": "未连接",
    "Manage": "管理",
    "Reconnect": "重新连接",
    "Sign-in expired": "登录已过期",
    "Set on the server": "已在服务器上设置",
    "Not available": "不可用",
    "Needs attention": "需要处理",
    "Key saved": "密钥已保存",
    "Key needed": "需要密钥",
    "Add key": "添加密钥",
    "Optional": "可选",
    "Close": "关闭",
    "Calendar and email": "日历和邮件",
    "Calendar": "日历",
    "Assignments and notes": "作业和笔记",
    "Lights and devices": "灯和设备",
    "Music": "音乐",
    "Your API key": "你的 API 密钥",
    "Your calendar, and your email if your account includes it. I only ever write drafts; you send them.":
      "你的日历，以及账户里有的邮件。我只写草稿，由你自己发送。",
    "Your Notion assignments and notes, so I can list what's due and file your notes.":
      "你的 Notion 作业和笔记，这样我能列出要交的作业、整理你的笔记。",
    "Your school deadlines from Canvas. I never ask for your school password.":
      "来自 Canvas 的学校截止日期。我从不索要你的学校密码。",
    "Your lights and devices. Locks, garage doors and alarms always ask you first.":
      "你的灯和设备。门锁、车库门和警报总会先问你。",
    "Play music and control playback on your devices.": "在你的设备上播放音乐、控制播放。",
    // the owner's own sign-ins (connect.js, owner_connect.py)
    "Set on the server.": "已在服务器上设置。",
    "Not set up on this server.": "这台服务器还没有设置好。",
    "This server can't store sign-ins yet (USER_DATA_KEY).": "这台服务器暂时还不能保存登录（缺少 USER_DATA_KEY）。",
    "The sign-in stopped working. Connect again.": "登录已失效，请重新连接。",
    "Connect": "连接",
    "Disconnect": "断开",
    "Home Assistant's public https address": "Home Assistant 的公网 https 地址",
    "Type your Home Assistant's public https address.": "请输入 Home Assistant 的公网 https 地址。",
    "Kwil reaches it over the internet: use Nabu Casa's address or a tunnel.":
      "Kwil 通过互联网访问它：请用 Nabu Casa 的地址或隧道。",
    "Opening the sign-in…": "正在打开登录页面…",
    "Couldn't start the sign-in.": "无法开始登录。",
    "Check the address: it must be your Home Assistant's public https address.":
      "请检查地址：必须是你的 Home Assistant 的公网 https 地址。",
    "Disconnect Home Assistant? I won't control your home any more.":
      "要断开 Home Assistant 吗？之后我就不能控制你家里的设备了。",
    "Disconnected.": "已断开。",
    // a student's deadlines card (student.js, student_web.py)
    "School deadlines": "学校截止日期",
    "Canvas calendar feed link": "Canvas 日历订阅链接",
    "In Canvas, open Calendar, tap Calendar Feed and copy the link. I never ask for your school password.":
      "在 Canvas 里打开“日历”，点“日历订阅”，复制链接。我绝不会问你的学校密码。",
    "Connect Canvas": "连接 Canvas",
    "Your school's name or Canvas address": "你学校的名字或 Canvas 地址",
    "Find your school": "查找你的学校",
    "Find my school": "查找我的学校",
    "Open my Canvas calendar": "打开我的 Canvas 日历",
    "Tap Calendar Feed (bottom right), copy the link, come back.":
      "点右下角的“日历订阅”，复制链接，再回到这里。",
    "Paste": "粘贴",
    "Type at least 3 letters of your school's name.": "请至少输入学校名字的 3 个字母。",
    "Couldn't look up schools. Try again.": "没能查找学校，请再试一次。",
    "I couldn't find that school. Type its Canvas address, e.g. canvas.school.edu.":
      "没有找到这所学校。请输入它的 Canvas 地址，例如 canvas.school.edu。",
    "Copy the Calendar Feed link in Canvas first.": "请先在 Canvas 里复制“日历订阅”链接。",
    "Canvas access token": "Canvas 访问令牌",
    "Connect with token": "用令牌连接",
    "Open my Canvas settings": "打开我的 Canvas 设置",
    "Tap New access token, pick an expiry date (a student's token lasts at most 120 days), copy the token, come back.":
      "点“新建访问令牌”，选一个过期日期（学生的令牌最长有效 120 天），复制令牌，再回到这里。",
    "My Canvas has no New access token button": "我的 Canvas 没有“新建访问令牌”按钮",
    "Use the calendar feed instead": "改用日历订阅链接",
    "For course files too, set up the Kwil Canvas helper in Chrome or Edge on a computer.":
      "如果还想要课程文件，请在电脑上的 Chrome 或 Edge 里设置 Kwil Canvas 助手。",
    "Get course files too": "也获取课程文件",
    "Thanks. Here is another way in for your school.": "谢谢。这是适合你学校的另一种连接方式。",
    "Sync now": "立即同步",
    "Disconnect Canvas": "断开 Canvas",
    "What I captured": "我记下的内容",
    "Nothing captured yet.": "还没有记下任何内容。",
    "Looks right": "没错",
    "This date is right": "这个日期是对的",
    "Not this one": "不是这个",
    "Dates disagree": "日期不一致",
    "Not checked": "未确认",
    "Canvas calendar": "Canvas 日历",
    "Canvas": "Canvas",
    "Canvas pages": "Canvas 页面",
    "Canvas email": "Canvas 邮件",
    "Screenshot": "截图",
    "Added by you": "你添加的",
    "Canvas connected.": "Canvas 已连接。",
    "Canvas connected, and Canvas emails in Gmail.": "Canvas 已连接，也会读 Gmail 里的 Canvas 邮件。",
    "Put CANVAS_ICS_URL (or CANVAS_TOKEN) in your server's .env to connect Canvas.":
      "在服务器的 .env 里设置 CANVAS_ICS_URL（或 CANVAS_TOKEN）来连接 Canvas。",
    "This server can't store sign-ins yet.": "这台服务器暂时还不能保存登录信息。",
    "Connect Canvas and I'll keep track of your deadlines.": "连接 Canvas，我就会帮你记住截止日期。",
    "Couldn't connect Canvas.": "没能连接 Canvas。",
    "Connected. Syncing…": "已连接，正在同步…",
    "Couldn't connect Canvas. Try again.": "没能连接 Canvas，请再试一次。",
    "Couldn't sync. Try again.": "没能同步，请再试一次。",
    "Nothing new.": "没有新内容。",
    "Syncing in the background.": "正在后台同步。",
    "Synced, but some sources didn't answer. Try reconnecting.": "已同步，但有些来源没有回应。请试试重新连接。",
    "Disconnect Canvas? I'll forget the link.": "要断开 Canvas 吗？我会忘掉这个链接。",
    "Also delete what I captured from Canvas?": "也删除我从 Canvas 记下的内容吗？",
    "Canvas disconnected.": "Canvas 已断开。",
    "Couldn't save that. Try again.": "没能保存，请再试一次。",
    "Delete this deadline?": "要删除这个截止日期吗？",
    "Couldn't delete it. Try again.": "没能删除，请再试一次。",
    "Canvas helper": "Canvas 助手",
    "For schools that block access tokens: the Kwil Canvas helper reads your own courses with your own sign-in, like opening the pages yourself. Some schools' rules limit automated access; check yours. It is read-only and rate-limited.":
      "适用于不允许使用访问令牌的学校：Kwil Canvas 助手用你自己的登录读取你自己的课程，就像你自己打开这些页面一样。有些学校的规定限制自动访问，请先确认你学校的规定。它只读取，不做任何修改，而且有频率限制。",
    "Open the Kwil Canvas helper in this browser: it picks this code up by itself. It works once, for 10 minutes.":
      "在这个浏览器里打开 Kwil Canvas 助手：它会自己读取这个配对码。配对码只能用一次，10 分钟内有效。",
    "Set up Canvas helper": "设置 Canvas 助手",
    "Revoke helper": "撤销助手",
    "Canvas helper connected.": "Canvas 助手已连接。",
    "Couldn't start pairing. Try again.": "没能开始配对，请再试一次。",
    "Revoke the Canvas helper? It stops sending your courses; what it sent stays until you disconnect.":
      "要撤销 Canvas 助手吗？它将不再发送你的课程；已经发送的内容会保留，直到你断开 Canvas。",
    "Canvas helper revoked.": "Canvas 助手已撤销。",
    "Couldn't revoke the helper. Try again.": "没能撤销助手，请再试一次。",
    // notifications and the study brief (brief.js, push.py, proactive.py)
    "Enable notifications": "开启通知",
    "Turn off notifications": "关闭通知",
    "Notifications are on for this phone.": "这部手机的通知已开启。",
    "Notifications are off.": "通知已关闭。",
    "Notifications are blocked for this app. Turn them on in the phone's Settings, then try again.":
      "这个应用的通知被关闭了。请在手机的“设置”里打开，然后再试一次。",
    "To get notifications on an iPhone, add this app to your Home Screen (Share, then Add to Home Screen) and open it from there.":
      "要在 iPhone 上收到通知，请先把这个应用加到主屏幕（点“分享”，再点“添加到主屏幕”），然后从主屏幕打开。",
    "This server can't store notification settings yet.": "这个服务器暂时还不能保存通知设置。",
    "Couldn't turn on notifications. Try again.": "没能开启通知，请再试一次。",
    "Couldn't play the brief.": "无法播放简报。",
    "Latest brief": "最新简报",
    "Your week": "你的这一周",
    "Brief": "简报",
    "Play": "播放",
    "Disconnect Google account": "断开 Google 账户",
    "Disconnect your Google account? I won't see your calendar or email any more.":
      "要断开你的 Google 账户吗？之后我就看不到你的日历和邮件了。",
    "Google account disconnected.": "Google 账户已断开。",
    "Couldn't disconnect. Try again.": "没能断开，请再试一次。",
    // the cloud server owner's own (google_web.py, [webapp] owner_google)
    "Disconnect the Google account connected here? I won't see your calendar or email through it any more. This may also sign out Kwil's Google on your PC (same Google project).":
      "要断开在这里连接的 Google 账户吗？之后我就不能再通过它看你的日历和邮件了。这也可能会让电脑上的 Kwil 退出 Google（同一个 Google 项目）。",
    "Disconnect the Google account connected here? I won't see your calendar through it any more. This may also sign out Kwil's Google on your PC (same Google project).":
      "要断开在这里连接的 Google 账户吗？之后我就不能再通过它看你的日历了。这也可能会让电脑上的 Kwil 退出 Google（同一个 Google 项目）。",
    // a family member's own account (family.js setupAccount, account.py)
    "Download my data": "下载我的数据",
    "Delete my account": "删除我的账户",
    "This deletes your account, your conversations and your connected Google account from this app. It can't be undone.":
      "这会从这个应用里删除你的账户、你的对话和你连接的 Google 账户。删除后无法恢复。",
    "Type DELETE (or your name) to confirm": "输入 DELETE（或你的名字）确认",
    "Delete everything": "全部删除",
    "Preparing your download…": "正在准备下载…",
    "Your download has started.": "已开始下载。",
    "A download is already being made. Wait for it.": "正在准备另一个下载，请稍等。",
    "Too many downloads. Try again in an hour.": "下载次数太多了，请一小时后再试。",
    "Your data is too large to download here. Ask the person who invited you for a copy.":
      "你的数据太大，无法在这里下载。请让邀请你的人帮你导出一份。",
    "Couldn't download. Try again.": "下载失败，请再试一次。",
    "Type DELETE (or your name) to confirm.": "请输入 DELETE（或你的名字）确认。",
    "Delete your account and everything in it? This can't be undone.":
      "要删除你的账户和里面的所有内容吗？删除后无法恢复。",
    "Deleting…": "正在删除…",
    "Couldn't delete your account. Try again.": "没能删除你的账户，请再试一次。",
    "Invite someone": "邀请家人",
    "Name": "名字",
    "Language": "语言",
    "Time zone": "时区",
    "Create invite link": "生成邀请链接",
    "Invite link": "邀请链接",
    "Invite link ready.": "邀请链接已准备好。",
    "Invite link copied.": "邀请链接已复制。",
    "Copy the invite link below.": "请复制下面的邀请链接。",
    "Active": "已加入",
    "Invited": "已邀请",
    "Active · Beta": "已加入 · 测试版",
    "Invited · Beta": "已邀请 · 测试版",
    "Account": "账户类型",
    "Beta (Calendar only)": "测试版（只有日历）",
    "Family (Calendar and Gmail)": "家人（日历和 Gmail）",
    "Google isn't set up for beta accounts yet.": "测试版账户暂时还不能连接 Google。",
    "Disconnect your Google account? I won't see your calendar any more.":
      "要断开你的 Google 账户吗？之后我就看不到你的日历了。",
    // a person's own Anthropic key (family.js setupKey, userkeys_web.py)
    "Your Anthropic API key": "你的 Anthropic API 密钥",
    "Anthropic API key": "Anthropic API 密钥",
    "Save key": "保存密钥",
    "Replace key": "更换密钥",
    "Remove key": "删除密钥",
    ["You pay Anthropic for what you use. To get a key, sign in at console.anthropic.com, " +
      "open API Keys, tap Create Key, and paste it here."]:
      "用多少由你自己向 Anthropic 付费。获取密钥：登录 console.anthropic.com，" +
      "打开 API Keys，点 Create Key，然后粘贴到这里。",
    "This server can't store keys yet.": "这个服务器暂时还不能保存密钥。",
    "Your saved key can't be read any more. Please enter it again.":
      "你保存的密钥已经读不出来了，请重新输入。",
    "Add your key to start talking with Claude.": "添加你的密钥，就可以开始和 Claude 对话了。",
    "Optional: without one, the family's key is used.": "可选：不添加的话，会用家里的密钥。",
    "Checking the key with Anthropic…": "正在向 Anthropic 核对密钥…",
    "Remove your Anthropic API key from this app?": "要从这个应用里删除你的 Anthropic API 密钥吗？",
    "that doesn't look like an Anthropic API key (it starts with sk-ant-)":
      "这看起来不像 Anthropic API 密钥（它以 sk-ant- 开头）",
    "paste your Anthropic API key": "请粘贴你的 Anthropic API 密钥",
    "Anthropic didn't accept that key": "Anthropic 没有接受这个密钥",
    "couldn't reach Anthropic to check the key; try again": "连不上 Anthropic 核对密钥，请再试一次",
    "couldn't check the key with Anthropic; try again": "暂时没法向 Anthropic 核对密钥，请再试一次",
    "too many tries; wait a minute": "尝试次数太多了，请等一分钟",
    "this server can't store keys yet (its owner needs to set USER_DATA_KEY)":
      "这个服务器暂时还不能保存密钥（需要管理员设置 USER_DATA_KEY）",
    "couldn't save the key; try again": "密钥没能保存，请再试一次",
    "couldn't remove the key; try again": "密钥没能删除，请再试一次",
    "New link": "新链接",
    "Remove": "移除",
    "This invite link has expired or was already used. Ask for a new one.":
      "这个邀请链接已过期或已经用过了，请再要一个新的。",
    "this invite link has expired or was already used": "这个邀请链接已过期或已经用过了。",
    "Couldn't reach the server. Open the link again.": "连不上服务器，请再打开一次链接。",
    // the Memory screen (memory.js)
    "Memory": "记忆",
    "Summary": "对话摘要",
    "Note": "笔记",
    "Untrusted": "来源不可信",
    "Saved while a web page or email was in the conversation": "保存时对话里有网页或邮件内容",
    "Date unknown": "日期未知",
    "Last week": "上周",
    "Delete all from this week": "删除本周的全部记忆",
    "Delete all from that week": "删除那一周的全部记忆",
    "Delete all with no date": "删除所有日期未知的记忆",
    "Delete this memory? This can't be undone.": "要删除这条记忆吗？删除后无法恢复。",
    "Nothing saved yet.": "还没有保存任何记忆。",
    "Showing the newest 1000 only.": "只显示最新的 1000 条。",
    "couldn't reach long-term memory; try again": "连不上长期记忆，请再试一次。",
    "not found": "找不到。",
    // reply-voice picker (voice-picker.js)
    "Reply voice": "回复的声音",
    "Preview": "试听",
    "paid": "付费",
    "ElevenLabs voice ID": "ElevenLabs 声音 ID",
    "Use this voice": "使用这个声音",
    "Saved.": "已保存。",
    "Couldn't play the preview.": "无法播放试听。",
    "unknown voice": "没有这个声音。",
    "System voice": "系统声音",
    "couldn't save the voice; try again": "声音没有保存成功，请再试一次。",
    "too many previews; try again in a minute": "试听次数太多，请一分钟后再试。",
    "couldn't check that voice with ElevenLabs; try again": "无法向 ElevenLabs 确认这个声音，请再试一次。",
    // tabs, settings, history, lecture and tutor screens
    "Backends": "服务器",
    "Use": "使用",
    "Auto": "Pi, then PC, then cloud\": \"自动：先树莓派，再电脑，再云端",
    "Pi only": "只用树莓派",
    "PC only": "只用电脑",
    "Cloud only": "只用云端",
    "Pi (tailnet)": "树莓派（tailnet）",
    "PC (tailnet)": "电脑（tailnet）",
    "Cloud": "云端",
    "Empty = the address the server suggests.": "留空 = 使用服务器建议的地址。",
    "Save and check": "保存并检查",
    "CC": "字幕",
    "All": "全部",
    "Pinned": "已置顶",
    "Phone": "手机",
    "Voice": "语音",
    "PC": "电脑",
    "Typed": "打字",
    "Archived": "已归档",
    "Open": "打开",
    "Pin": "置顶",
    "Unpin": "取消置顶",
    "Rename": "重命名",
    "Archive": "归档",
    "Unarchive": "取消归档",
    "Export as Markdown": "导出为 Markdown",
    "Delete…": "删除…",
    "Delete conversation?": "删除这个对话？",
    "this app's address": "这个应用的地址",
    "Filter": "筛选",
    "Back to history": "返回历史记录",
    "More actions": "更多操作",
    "Chat / Lecture / Tutor": "聊天 / 讲座 / 作业辅导",
    "Chat": "聊天",
    "Lecture": "讲座",
    "Tutor": "作业辅导",
    "Course": "课程",
    "Course (optional)": "课程（可选）",
    "Start recording": "开始录音",
    "Stop recording": "停止录音",
    "Recent lectures": "最近的讲座",
    "Uploaded. Transcribing on the server...": "已上传，正在服务器上转写…",
    "Microphone not allowed.": "没有麦克风权限。",
    "Waiting": "等待中",
    "Summarizing": "正在总结",
    "Saving to Notion": "正在保存到 Notion",
    "Done": "完成",
    "Failed": "失败",
    "Open the notes in Notion": "在 Notion 里打开笔记",
    "Something went wrong.": "出了点问题。",
    "Try again": "再试一次",
    "These look like assignments. Add any to Notion?": "这些看起来像作业，要加到 Notion 吗？",
    "Added": "已添加",
    "No lectures yet.": "还没有讲座。",
    "The problem": "题目",
    "What are you stuck on? (optional)": "哪里不会？（可选）",
    "Take photo": "拍照",
    "Choose photo": "选择照片",
    "Show the full solution": "显示完整解答",
    "Read explanations aloud": "朗读讲解",
    "Preparing the photo...": "正在准备照片…",
    "Photo ready. Ask a question, or just tap Ask.": "照片好了。可以提问，或直接点“提问”。",
    "That photo couldn\"t be opened. Try a JPEG or PNG.": "这张照片打不开，请换一张 JPEG 或 PNG。",
    "Take or choose a photo first.": "请先拍照或选择照片。",
    "Next step?": "下一步？",
    "Thinking...": "正在思考…",
    "Ask": "提问",
    "Spoken to the PC": "对电脑说的",
    "Phone voice mode": "手机语音对话",
    "Incognito": "nothing from now on is saved.\": \"无痕模式：从现在起不保存。",
    "Incognito off": "conversations are saved again.\": \"已关闭无痕模式：对话会再次保存。",
    "Today": "今天",
    "Yesterday": "昨天",
    "This week": "本周",
    "Older": "更早",
    "Results": "搜索结果",
    "Nothing here.": "这里还没有内容。",
    "No saved conversations yet.": "还没有保存的对话。",
    "Turn incognito off to continue it.": "关闭无痕模式才能继续这个对话。",
    "Deleted.": "已删除。",
    "Can\"t reach the Pi, the PC or the cloud right now.": "现在连不上树莓派、电脑或云端。",
    "No sound is coming from this microphone. If you\"re on CarPlay or Bluetooth, try push-to-talk.": "这个麦克风没有声音。如果在用 CarPlay 或蓝牙，请改用按住说话。",
    "Auto: Pi, then PC, then cloud": "自动：先树莓派，再电脑，再云端",
    "That photo couldn't be opened. Try a JPEG or PNG.": "这张照片打不开，请换一张 JPEG 或 PNG。",
    "Incognito: nothing from now on is saved.": "无痕模式：从现在起不保存。",
    "Incognito off: conversations are saved again.": "已关闭无痕模式：对话会再次保存。",
    "Can't reach the Pi, the PC or the cloud right now.": "现在连不上树莓派、电脑或云端。",
    "No sound is coming from this microphone. If you're on CarPlay or Bluetooth, try push-to-talk.": "这个麦克风没有声音。如果在用 CarPlay 或蓝牙，请改用按住说话。",
    "Things you can ask me (hold the mic and talk, or type):": "你可以这样问我（按住麦克风说话，或者打字）：",
    "\"What's the weather tomorrow?\"": "“明天天气怎么样？”",
    "\"How do I make braised pork belly?\"": "“红烧肉怎么做？”",
    "\"How do you say 'thank you for dinner' in English?\"": "“‘谢谢你请我吃饭’用英文怎么说？”",
    "\"Help me write a birthday message to my friend.\"": "“帮我给朋友写一段生日祝福。”",
    "\"What's on my calendar tomorrow?\"": "“我明天有什么安排？”",
    "\"Add a doctor's appointment Friday at 10am.\"": "“星期五上午十点加一个看医生的预约。”",
    "\"Any new email today?\"": "“今天有新邮件吗？”",
    "\"Help me reply to that email.\" (I only write a draft; you send it.)": "“帮我回复那封邮件。”（我只写草稿，你自己发送。）",
    // attachments ("+" left of the text field, attachments.js / attachments.py)
    "Add photos or files": "添加照片或文件",
    "Add to your message": "添加到消息",
    "Photo library": "照片图库",
    "Choose file": "选择文件",
    "Attachments": "附件",
    "You can attach up to 4 files.": "最多可以附加 4 个文件。",
    "Only photos, PDFs and text files can be attached.": "只能附加照片、PDF 和文本文件。",
    "Attach at most 4 files to a message": "每条消息最多附加 4 个文件。",
    "bad request (attach at most 4 files)": "请求有误（最多附加 4 个文件）。",
    "bad request": "请求有误。",
    "A PDF is over 100 pages": "有一个 PDF 超过 100 页。",
    "That file is too long for Claude (PDFs up to 100 pages).":
      "这个文件太长了，Claude 处理不了（PDF 最多 100 页）。",
    "Claude couldn't read that file.": "Claude 读不了这个文件。",
    "Only photos (JPEG, PNG, WebP, GIF), PDFs and text files can be attached":
      "只能附加照片（JPEG、PNG、WebP、GIF）、PDF 和文本文件。",
    "A photo couldn't be read": "有一张照片读不出来。",
    "That file is empty": "这个文件是空的。",
    "A text file is too long to send": "文本文件太长，发不了。",
    "too large": "文件太大了。",
    "request too large": "文件太大了。",
    // first-run guide (data-google instances: a family member's own app)
    "Enter the sign-in code you were sent. You only need to do this once on this phone.":
      "请输入发给你的登录码。这部手机只需要输入一次。",
    "Welcome! A few one-time steps:": "欢迎！只需要做一次的几个步骤：",
    "Got it": "知道了",
    ["Add this app to your home screen: in Safari, tap the Share button (the square " +
      "with an arrow) at the bottom, then \"Add to Home Screen\", and open it from there."]:
      "把这个应用加到主屏幕：在 Safari 里点下面的分享按钮（带箭头的方框），" +
      "再点“添加到主屏幕”，以后从主屏幕打开。",
    ["Connect your own Google account so I can see your calendar and email: tap the gear at " +
      "the top right, then \"Google\" under Connections and \"Connect Google account\", and sign in with your own Google " +
      "account. If Google says it hasn't verified " +
      "this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
      "app was made by your family, not a company."]:
      "连接你的 Google 账户，我就能看你的日历和邮件：点右上角的齿轮，在“服务连接”里点“Google”，再点“连接 Google 账户”，" +
      "用你自己的 Google 账户登录。" +
      "如果 Google 说“此应用未经验证”，点“高级”，再点“转至…（不安全）”。这是正常的：" +
      "这个应用是你的家人做的，不是公司做的。",
    ["Connect your own Google account so I can see your calendar: tap the gear at " +
      "the top right, then \"Google\" under Connections and \"Connect Google account\", and sign in with your own Google " +
      "account. If Google says it hasn't verified " +
      "this app, tap \"Advanced\", then \"Go to … (unsafe)\"."]:
      "连接你的 Google 账户，我就能看你的日历：点右上角的齿轮，在“服务连接”里点“Google”，再点“连接 Google 账户”，" +
      "用你自己的 Google 账户登录。" +
      "如果 Google 说“此应用未经验证”，点“高级”，再点“转至…（不安全）”。",
  };
  const PATTERNS = [
    [/^Working: (.+)…$/, "正在处理：$1…"],
    [/^Error (\d+)$/, "错误 $1"],
    [/^Microphone unavailable: (.*)$/, "麦克风不可用：$1"],
    [/^Remove (.+)\? This deletes their conversations\.$/, "要移除$1吗？这会删除他们的对话记录。"],
    [/^Voice mode couldn't start: (.*)$/, "语音模式无法启动：$1"],
    [/^Actions for (.+)$/, "$1 的操作"],
    [/^No conversations match “(.+)”\.$/, "没有和“$1”相符的对话。"],
    [/^(\d+) messages$/, "$1 条消息"],
    [/^Continuing “(.+)”\. Go ahead\.$/, "继续“$1”，请说。"],
    [/^Asked: (.*)$/s, "问：$1"],
    [/^Answered: (.*)$/s, "答：$1"],
    [/^Transcribing (\d+)%$/, "正在转写 $1%"],
    [/^Uploaded (.+)$/, "已上传 $1"],
    [/^offline, retrying in (\d+) s$/, "离线，$1 秒后重试"],
    [/^(.+) \(in use\)$/, "$1（正在使用）"],
    [/^A photo is over ([\d.]+) MB$/, "有一张照片超过 $1 MB。"],
    [/^A file is over ([\d.]+) MB$/, "有一个文件超过 $1 MB。"],
    [/^Week of (.+)$/, "$1 那一周"],
    [/^Deleted (\d+)\.$/, "已删除 $1 条。"],
    [/^Delete all (\d+) notes? from this week\? This can't be undone\.$/, "要删除本周的全部 $1 条记忆吗？删除后无法恢复。"],
    [/^Delete all (\d+) notes? from last week\? This can't be undone\.$/, "要删除上周的全部 $1 条记忆吗？删除后无法恢复。"],
    [/^Delete all (\d+) notes? from the week of (.+)\? This can't be undone\.$/, "要删除 $2 那一周的全部 $1 条记忆吗？删除后无法恢复。"],
    [/^Delete all (\d+) notes? with no date\? This can't be undone\.$/, "要删除日期未知的全部 $1 条记忆吗？删除后无法恢复。"],
    [/^Default \((.+)\)$/, "默认（$1）"],
    [/^(.+) · paid$/, "$1 · 付费"],
    [/^Your key ending in (\S{1,4}) is saved\.$/, "你的密钥（结尾 $1）已保存。"],
    [/^About \$(\d+\.\d\d) this month on your key\.$/, "这个月用你的密钥大约花了 $$$1。"],
    [/^Found (\d+) new\.$/, "找到 $1 条新内容。"],
  ];
  const ATTRS = ["placeholder", "aria-label", "title"];
  // The line a message with attachments ends with, "[photo] [file: a.pdf]"
  // (attachments.js summary(), and the marker saved in history): shown in
  // Chinese, kept in English where it is stored.
  const MARKER = /\[photo\]|\[file: [^\]\n]+\]/;
  const MARKER_LINE = new RegExp(`(^|\\n)((?:${MARKER.source})(?: (?:${MARKER.source}))*)(\\s*)$`);

  function translateMarkers(text) {
    return text.replace(MARKER_LINE, (all, lead, line, trail) => lead + line
      .replace(/\[photo\]/g, "[照片]")
      .replace(/\[file: ([^\]\n]+)\]/g, "[文件：$1]") + trail);
  }

  // The Chinese for a known English string (whitespace kept), else `text`.
  function translate(text) {
    if (typeof text !== "string") return text;
    const key = text.replace(/\s+/g, " ").trim();
    if (!key) return text;
    let out = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : undefined;
    if (out === undefined) {
      for (const [re, rep] of PATTERNS) {
        if (re.test(key)) { out = key.replace(re, rep); break; }
      }
    }
    if (out === undefined) return translateMarkers(text);
    // keep the spacing, except next to full-width punctuation ("，在电脑的")
    const lead = /^[，。、：；）]/.test(out) ? "" : text.match(/^\s*/)[0];
    const trail = /[（]$/.test(out) ? "" : text.match(/\s*$/)[0];
    return lead + out + trail;
  }

  function translateAttrs(el) {
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!value) continue;
      const t = translate(value);
      if (t !== value) el.setAttribute(name, t);
    }
  }

  function translateNode(node) {
    if (!node) return;
    if (node.nodeType === 3) {  // text
      const t = translate(node.nodeValue);
      if (t !== node.nodeValue) node.nodeValue = t;
      return;
    }
    if (node.nodeType !== 1 || node.tagName === "SCRIPT" || node.tagName === "STYLE") return;
    translateAttrs(node);
    for (const child of Array.from(node.childNodes)) translateNode(child);
  }

  function isActive(doc) {
    const lang = (doc && doc.documentElement && doc.documentElement.getAttribute("lang")) || "";
    return lang.toLowerCase().startsWith("zh");
  }

  // Translate now, then everything app.js / voice.js add or change later.
  function start(win, doc) {
    translateNode(doc.body);
    if (typeof win.MutationObserver === "function") {
      new win.MutationObserver((records) => {
        for (const r of records) {
          if (r.type === "childList") r.addedNodes.forEach(translateNode);
          else if (r.type === "characterData") translateNode(r.target);
          else if (r.type === "attributes") translateAttrs(r.target);
        }
      }).observe(doc.body, { subtree: true, childList: true, characterData: true,
                             attributes: true, attributeFilter: ATTRS });
    }
    for (const name of ["confirm", "alert"]) {
      const original = win[name];
      if (typeof original === "function") {
        win[name] = (message) => original.call(win, translate(String(message)));
      }
    }
  }

  // --- first-run guide: the steps only she can do on her phone ---
  const STEP_HOME = "Add this app to your home screen: in Safari, tap the Share button (the " +
    "square with an arrow) at the bottom, then \"Add to Home Screen\", and open it from there.";
  // (a family member's own account on the shared server, google_web.py)
  const STEP_GOOGLE = "Connect your own Google account so I can see your calendar and email: tap " +
    "the gear at the top right, then \"Google\" under Connections and \"Connect Google account\", and sign in with your own " +
    "Google account. If Google says it hasn't " +
    "verified this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
    "app was made by your family, not a company.";
  // a beta tester's (status "gmail": false): Calendar only, no mail wording
  const STEP_GOOGLE_CALENDAR = "Connect your own Google account so I can see your calendar: " +
    "tap the gear at the top right, then \"Google\" under Connections and \"Connect Google account\", and sign in with your " +
    "own Google account. If Google says it hasn't verified this app, tap \"Advanced\", " +
    "then \"Go to … (unsafe)\".";

  // Shown (with the steps, or alone once they're done) until she taps Got it.
  const EXAMPLES_ALWAYS = ["\"What's the weather tomorrow?\"",
    "\"How do I make braised pork belly?\"",
    "\"How do you say 'thank you for dinner' in English?\"",
    "\"Help me write a birthday message to my friend.\""];
  const EXAMPLES_CALENDAR = ["\"What's on my calendar tomorrow?\"",
    "\"Add a doctor's appointment Friday at 10am.\""];
  const EXAMPLES_GOOGLE = [...EXAMPLES_CALENDAR,
    "\"Any new email today?\"",
    "\"Help me reply to that email.\" (I only write a draft; you send it.)"];

  function standalone(win) {
    try {
      return win.navigator?.standalone === true ||
        !!(win.matchMedia && win.matchMedia("(display-mode: standalone)").matches);
    } catch (e) { return false; }
  }

  function renderGuide(win, doc, st, say) {
    const box = doc.getElementById("guide");
    const text = doc.getElementById("guide-text");
    if (!box || !text) return;
    let dismissed = false;
    try { dismissed = win.sessionStorage?.getItem("guideDismissed") === "1"; } catch (e) { /* private */ }
    const steps = [];
    if (!standalone(win)) steps.push(STEP_HOME);
    const mail = st.gmail !== false;  // false: a beta tester, Calendar only
    if (st.enabled && !st.connected) steps.push(mail ? STEP_GOOGLE : STEP_GOOGLE_CALENDAR);
    let seen = false;
    try { seen = win.localStorage?.getItem("examplesSeen") === "1"; } catch (e) { /* private */ }
    const examples = seen ? [] : [...EXAMPLES_ALWAYS,
      ...(st.connected ? (mail ? EXAMPLES_GOOGLE : EXAMPLES_CALENDAR) : [])];
    if ((!steps.length && !examples.length) || dismissed) { box.hidden = true; return; }
    const lines = [];
    if (steps.length) {
      lines.push(say("Welcome! A few one-time steps:"),
                 ...steps.map((step, i) => `${i + 1}. ${say(step)}`));
    }
    if (examples.length) {
      if (lines.length) lines.push("");
      lines.push(say("Things you can ask me (hold the mic and talk, or type):"),
                 ...examples.map((e) => `• ${say(e)}`));
    }
    text.textContent = lines.join("\n");
    box.setAttribute("data-examples", examples.length ? "1" : "");
    box.hidden = false;
  }

  // --- "Connect Google account" (google_web.py) ---
  function setupGoogle(win, doc) {
    const btn = doc.getElementById("google-connect");
    const hint = doc.getElementById("google-status");
    const unlink = doc.getElementById("google-disconnect");
    if (!btn) return null;
    const say = (text) => (isActive(doc) ? translate(text) : text);
    const login = doc.getElementById("login-text");
    if (login) {
      login.textContent = say("Enter the sign-in code you were sent. You only need to do this once on this phone.");
    }
    const done = doc.getElementById("guide-done");
    if (done) {
      done.addEventListener("click", () => {
        const box = doc.getElementById("guide");
        try {
          win.sessionStorage?.setItem("guideDismissed", "1");
          if (box && box.getAttribute("data-examples") === "1") win.localStorage?.setItem("examplesSeen", "1");
        } catch (e) { /* private */ }
        if (box) box.hidden = true;
      });
    }

    // the Google row in Connections: its status, action word and subtitle
    // (a beta tester's is Calendar only); a sign-in Google refused needs you
    function rowOf(st) {
      const sub = st.gmail === false ? "Calendar" : "Calendar and email";
      if (st.connected && st.needs_reconnect === true) {
        return { state: "attention", status: "Sign-in expired", sub,
                 ...(st.enabled ? { action: "Reconnect" } : {}) };
      }
      if (st.enabled && st.connected) return { state: "connected", status: "Connected", action: "Manage", sub };
      if (st.enabled) return { state: "available", status: "Not connected", action: "Connect", sub };
      return st.note ? { state: "available", status: "Not available", sub } : { state: "off" };
    }

    let signedIn = false;
    let mail = true;  // the last status's "gmail" (false: no mail wording)
    let owner = false;  // the last status's "server_owner": the cloud owner's own
    async function refresh() {
      try {
        const res = await win.fetch("/api/google/status",
                                    { credentials: "same-origin", cache: "no-store" });
        if (!res.ok) return res.status;  // 401: not signed in yet; 404: the button is off
        const st = await res.json();
        signedIn = true;
        mail = st.gmail !== false;
        owner = st.server_owner === true;
        if (owner) {  // the first-run guide is for invited people, not the owner
          const box = doc.getElementById("guide");
          if (box) box.hidden = true;
        } else {
          renderGuide(win, doc, st, say);
        }
        btn.hidden = !st.enabled;
        btn.textContent = say(st.connected ? "Reconnect Google account" : "Connect Google account");
        if (unlink) unlink.hidden = !(st.enabled && st.connected && st.disconnect);
        if (hint && st.connected && st.needs_reconnect === true) {
          // Google refused the saved sign-in: never "connected"; the
          // button above runs the same connect flow
          hint.hidden = false;
          hint.textContent = say(st.enabled ? "Google sign-in expired. Reconnect to keep using it."
                                            : "Google sign-in expired.");
        } else if (hint && !st.enabled && st.note) {  // e.g. not set up for beta accounts yet
          hint.hidden = false;
          hint.textContent = say(String(st.note));
        } else if (hint) {
          hint.hidden = !(st.enabled && st.connected);
          hint.textContent = say("Google account connected.");
        }
        win.KwilConnections?.set("google", rowOf(st));  // connections.js
      } catch (e) { /* offline: leave it as it is */ }
    }

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (hint) { hint.hidden = false; hint.textContent = say("Opening Google…"); }
      try {
        const res = await win.fetch("/api/google/connect", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: "{}",
        });
        const body = res.ok ? await res.json() : null;
        if (body && typeof body.url === "string" && body.url.startsWith("https://")) {
          win.location.href = body.url;  // back to / after signing in
          return;
        }
      } catch (e) { /* shown below */ }
      btn.disabled = false;
      if (hint) hint.textContent = say("Couldn't start the Google sign-in.");
    });
    if (unlink) {
      unlink.addEventListener("click", async () => {
        // the owner's: grants in one Google project may be revoked together
        const ask = say(owner
          ? (mail
            ? "Disconnect the Google account connected here? I won't see your calendar or email through it any more. This may also sign out Kwil's Google on your PC (same Google project)."
            : "Disconnect the Google account connected here? I won't see your calendar through it any more. This may also sign out Kwil's Google on your PC (same Google project).")
          : mail
            ? "Disconnect your Google account? I won't see your calendar or email any more."
            : "Disconnect your Google account? I won't see your calendar any more.");
        if (typeof win.confirm === "function" && !win.confirm(ask)) return;
        unlink.disabled = true;
        let ok = false;
        try {
          const res = await win.fetch("/api/google/disconnect", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" }, body: "{}",
          });
          ok = res.ok;
        } catch (e) { /* shown below */ }
        unlink.disabled = false;
        if (ok) {
          await refresh();
          if (hint) { hint.hidden = false; hint.textContent = say("Google account disconnected."); }
        } else if (hint) {
          hint.hidden = false;
          hint.textContent = say("Couldn't disconnect. Try again.");
        }
      });
    }
    // After signing in the page doesn't reload: look again until it works.
    // Not while signed out: every 401 counts against the cloud's
    // per-address request limit (a campus NAT shares one), so a 401 stops
    // it and the sign-in form starts it again.
    let tries = 0;
    let timer = null;
    function stopPolling() {
      if (timer !== null) { win.clearInterval(timer); timer = null; }
    }
    function poll() {
      if (timer !== null || signedIn || typeof win.setInterval !== "function") return;
      tries = 0;
      timer = win.setInterval(() => {
        if (signedIn || ++tries > 200) { stopPolling(); return; }
        check();
      }, 3000);
    }
    async function check() {
      const status = await refresh();
      if (status === 401 || status === 404) stopPolling();  // signed out, or nothing here
    }
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });
    const form = doc.getElementById("login-form");
    if (form) form.addEventListener("submit", () => poll());
    poll();
    check();
    return { refresh };
  }

  const api = { ZH, translate, translateNode, isActive, start, setupGoogle };
  root.KwilI18n = api;
  if (typeof root.document !== "undefined") {
    const doc = root.document;
    if (isActive(doc)) start(root, doc);
    if (doc.documentElement && doc.documentElement.hasAttribute("data-google")) {
      api.google = setupGoogle(root, doc);
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
