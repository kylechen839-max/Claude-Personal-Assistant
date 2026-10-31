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
// the button reconnects. A token without the Drive scope (status
// "missing") says "Reconnect to add Google Drive (read-only)" there too.
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
    "Main navigation": "主导航",  // the phone's tab bar (aria-label)
    // the desktop rail's Search and the Ctrl+K switcher (ui.js); "Results"
    // (its list's label) is History's, below
    "Search": "搜索",
    "Go to": "前往",
    "Go to a screen or conversation": "前往页面或对话",
    "No matches": "没有匹配项",
    "New chat": "新对话",
    "Start voice conversation": "开始语音对话",
    "Conversation": "对话",
    // the Settings page's section heads (Voice, Connections, People and
    // Backends are below; "Account" is the invite form's field, not a head)
    "Memory and projects": "记忆和项目",
    "Notifications": "通知",
    "Privacy and data": "隐私和数据",
    "Your account": "你的账户",
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
    // the voice view's Camera button (voice.js CAMERA_TEXT)
    "Camera": "相机",
    "Camera on": "相机已开",
    "Send this frame": "发送这一帧",
    "Kwil can't use the camera. Allow it, then close and reopen Kwil.": "Kwil 用不了相机。请允许使用相机，然后关掉 Kwil 再重新打开。",
    "The camera couldn't start.": "相机没能打开。",
    "The camera stopped.": "相机停止了。",
    "That frame was too large to send; the turn went without it.": "这一帧太大，发不出去；这次没有带上画面。",
    "This frame goes with your next question.": "这一帧会和你的下一个问题一起发送。",
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
    "Still working on the last request.": "还在处理上一个请求。",
    // typing while Kwil is thinking (app.js): queued messages and Stop
    "Queued": "排队中",
    "Remove queued message": "移除排队的消息",
    "Stop": "停止",
    "Stopped.": "已停止。",
    "You can queue up to 3 messages.": "最多可以排队 3 条消息。",
    // try again, edit and branch (app.js, history.js)
    "Edit message": "编辑消息",
    "Editing your last message": "正在编辑你的上一条消息",
    "Cancel editing": "取消编辑",
    "Wait for the reply to finish first.": "请等回复结束后再试。",
    "This message": "这条消息",
    "Branch from here": "从这里分支",
    "Message actions": "消息操作",
    "Sign out on every phone and browser?": "要在所有手机和浏览器上退出登录吗？",
    "This browser can't record here. Open Kwil from its https:// address.":
      "这个浏览器在这里不能录音。请用 https:// 地址打开 Kwil。",
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
    // a token from before Google Drive (google_web.py status "missing")
    "Reconnect to add Google Drive (read-only)": "重新连接以添加 Google 云端硬盘（只读）",
    "Reconnect to add Google Drive (read-only).": "重新连接以添加 Google 云端硬盘（只读）。",
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
    "Disconnect this service?": "要断开这个服务吗？",  // connect.js's fallback question
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
    "Course files": "课程文件",
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
    "People": "家人",
    "Name": "名字",
    "Language": "语言",
    "Time zone": "时区",
    "For example, Asia/Shanghai": "例如 Asia/Shanghai",
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
    // a beta tester on the starter credit (family.js BETA_TEXT, credit.py)
    "Starter credit": "新手额度",
    "You're on the starter credit. Add your own key any time.":
      "你正在用新手额度，随时可以添加你自己的密钥。",
    "Your starter credit is used up. Add your key to keep talking with Claude.":
      "你的新手额度已用完。添加你的密钥，就能继续和 Claude 对话。",
    "How to get a key and what it costs": "怎么申请密钥，要花多少钱",
    // the invite's ready-to-send message (family.js share)
    "Invite message ready.": "邀请消息已准备好。",
    "Invite message copied.": "邀请消息已复制。",
    // beta feedback (family.js FEEDBACK_TEXT, feedback.py)
    "Send feedback": "发送反馈",
    "Feedback": "反馈",
    "How's the beta going?": "测试用得怎么样？",
    "Anything helps, even one line. Only the person who runs Kwil reads it.":
      "什么都有帮助，一句话也行。只有运行 Kwil 的人会看到。",
    "What broke?": "哪里出问题了？",
    "What confused you?": "哪里让你困惑？",
    "What would you pay for?": "你愿意为什么付费？",
    "How's Kwil going so far? Three quick questions help make it better.":
      "Kwil 用得怎么样？回答三个小问题，帮我们把它做得更好。",
    "Answer": "回答",
    "No thanks": "不用了",
    "Sending…": "正在发送…",
    "Thanks. Your feedback was sent.": "谢谢，反馈已发送。",
    "Write something first.": "请先写点内容。",
    "That's plenty for this hour, thank you. Try again later.": "这一小时已经发了很多条，谢谢。请稍后再试。",
    "You've sent a lot of feedback already. Thank you.": "你已经发了很多条反馈了，谢谢。",
    "That's too long. Shorten an answer.": "内容太长了，请缩短一些。",
    "Feedback can't be saved on this server yet.": "这个服务器暂时还不能保存反馈。",
    "Couldn't send it. Try again.": "没能发送，请再试一次。",
    "No feedback yet.": "还没有反馈。",
    "Couldn't load feedback. Try again.": "没能加载反馈，请再试一次。",
    "Survey": "问卷",
    "You": "你",
    // a beta tester's first-run checklist (CHECK_TEXT below)
    "Things to try": "可以先试试这些",
    "Hide": "隐藏",
    "Connect Canvas or Notion in Settings": "在“设置”里连接 Canvas 或 Notion",
    "Notion template": "Notion 模板",
    "Ask “What’s due this week?”": "问一句“这周有什么要交的？”",
    "Try voice mode with the round button below": "用下面的圆形按钮试试语音模式",
    "Join the beta testers' chat": "加入测试者群聊",
    "Open the chat": "打开群聊",
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
    "Outside source": "外部来源",
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
    // everyone's own memory (memory.js, memory_web.py)
    "Your instructions": "你的指示",
    "Things Kwil remembered": "Kwil 记住的事",
    "For example: always color-code my schedule.": "例如：日程安排一律用颜色区分。",
    "Kwil follows these from your next conversation.": "Kwil 从你的下一次对话开始照这些去做。",
    "Pause memory": "暂停记忆",
    "Export": "导出",
    "Forget all": "全部忘掉",
    "While paused, Kwil saves no new memories and uses none. Your instructions still apply.":
      "暂停时，Kwil 不会保存新的记忆，也不会用已有的记忆。你的指示照样有效。",
    "Long-term notes": "长期笔记",
    "Edit": "编辑",
    "Edit this memory": "编辑这条记忆",
    "Saved.": "已保存。",
    "Nothing remembered yet. Say “remember …” and Kwil saves it here.":
      "还没有记住任何事。说“记住……”，Kwil 就会存在这里。",
    "Forget everything Kwil remembered, your instructions and your study list? This can't be undone.":
      "要忘掉 Kwil 记住的所有事、你的指示和你的复习清单吗？忘掉后无法恢复。",
    "Forgot everything.": "已全部忘掉。",
    "Memory paused.": "记忆已暂停。",
    "Memory on.": "记忆已开启。",
    "Export started.": "已开始导出。",
    "Keep as mine": "确认是我的",
    "Kept as yours.": "已确认是你的。",
    "Saved on this server (cloud).": "保存在这台服务器上（云端）。",
    "Saved on this PC.": "保存在这台电脑上。",
    "Saved on this Raspberry Pi.": "保存在这台树莓派上。",
    "couldn't reach your memory; try again": "连不上你的记忆，请再试一次。",
    "instructions are at most 4000 characters": "指示最多 4000 个字。",
    "a memory is at most 500 characters": "一条记忆最多 500 个字。",
    "nothing to remember": "没有可以记住的内容。",
    "not found": "找不到。",
    // projects (projects.js, app.js, projects_web.py)
    "Projects": "项目",
    "Back to projects": "返回项目",
    "New project": "新建项目",
    "Rename project": "重命名项目",
    "Suggested from your courses": "根据你的课程推荐",
    "Each project has its own instructions, files, memories and chats. What Kwil remembers in a project stays in it.":
      "每个项目都有自己的说明、文件、记忆和对话。Kwil 在项目里记住的内容只留在这个项目里。",
    "New chat in this project": "在这个项目里开始新对话",
    "Instructions": "说明",
    "For example: PHYS 361, Classical Mechanics. Use Taylor's notation. Exams are closed-book.":
      "例如：PHYS 361，经典力学。用 Taylor 教材的记号。考试闭卷。",
    "Kwil follows these in this project's chats, from the next one.":
      "Kwil 会在这个项目的对话里遵守这些说明，从下一次对话开始。",
    "Files": "文件",
    "Add file": "添加文件",
    "Things Kwil remembered here": "Kwil 在这里记住的内容",
    "Chats": "对话",
    "Delete project": "删除项目",
    "Open project": "打开项目",
    "Leave project": "离开项目",
    "For example, PHYS 361": "例如：PHYS 361",
    "No projects yet.": "还没有项目。",
    "Connect Canvas or add deadlines, and your courses show here.":
      "连接 Canvas 或添加截止日期后，你的课程会显示在这里。",
    "Create": "创建",
    "Delete this project, its files and its memories? Its chats stay in History. This can't be undone.":
      "要删除这个项目、它的文件和记忆吗？它的对话会留在对话记录里。删除后无法恢复。",
    "Project deleted.": "项目已删除。",
    "Delete this file from the project? This can't be undone.":
      "要从项目里删除这个文件吗？删除后无法恢复。",
    "Adding…": "正在添加…",
    "Added.": "已添加。",
    "No files yet. Add a syllabus, notes or handouts (PDF or text).":
      "还没有文件。可以添加教学大纲、笔记或讲义（PDF 或文本）。",
    "Nothing remembered here yet. Say “remember …” in one of this project's chats.":
      "这里还没有记住的内容。在这个项目的对话里说“记住……”。",
    "No chats yet.": "还没有对话。",
    "New conversation": "新对话",
    "New chat.": "新对话。",
    "That project was deleted, so I didn't send this.": "那个项目已经删除了，所以这条消息没有发送。",
    "A project file is a PDF or a text file (attach photos to a message instead)":
      "项目文件只能是 PDF 或文本文件（照片请附在消息里）",
    "Your project files are at their limit": "你的项目文件已经到上限了",
    "couldn't reach your projects; try again": "无法读取你的项目，请重试",
    "bad request (one file at a time)": "请求有误（一次只能上传一个文件）",
    "send a form with one file": "请上传一个文件",
    // "think harder" and a reply's sources (app.js, sources.js, thinkhard.py)
    "Think harder": "仔细想想",
    "Send (hold to think harder)": "发送（按住可仔细想想）",
    "Think harder is on": "已开启仔细想想",
    "Turn off think harder": "关闭仔细想想",
    "Message": "消息",
    "Thinking harder…": "正在仔细想…",
    "Sources": "来源",
    "Source": "来源",  // sources.js's TEXT.source (its numbered labels are a PATTERN)
    "That's all your think-harder replies for today, so this one was answered the usual way.":
      "今天的“仔细想想”次数已经用完了，这次按平常的方式回答。",
    // study mode (study.js, memory.js, study.py)
    "Study mode": "学习模式",
    "Stop studying": "停止学习",
    "Studying": "正在学习",
    "Study mode on. Say what to study, or ask Kwil to quiz you.":
      "学习模式已开启。说说想学什么，或者让 Kwil 考考你。",
    "Study mode off.": "学习模式已关闭。",
    "Study: things to review": "学习：要复习的内容",
    "Nothing to review yet. When Kwil quizzes you, what you miss shows here.":
      "还没有要复习的内容。Kwil 考你时，答错的会显示在这里。",
    "Delete this from your study list? This can't be undone.":
      "要从复习清单里删除这一项吗？删除后无法恢复。",
    "Missed once": "错过 1 次",
    // Settings, Reminders (reminders.js, reminders.py)
    "Reminders": "提醒",
    "Missed": "错过的",
    "Say “remind me at 9 to …” or “every Monday at 8, tell me what's due”. Reminders arrive as notifications on this phone.":
      "说“9 点提醒我……”或“每周一 8 点告诉我这周要交什么”。提醒会以通知的形式发到这部手机上。",
    "Turn on notifications in Settings so reminders can arrive.": "请在设置里打开通知，提醒才能送达。",
    "No reminders yet.": "还没有提醒。",
    "Upcoming": "即将提醒",  // the Reminders list head (People's "Active" is 已加入)
    "Task": "任务",
    "Made while a web page or email was in the conversation": "创建时对话里有网页或邮件内容",
    "Pause": "暂停",
    "Resume": "继续",
    "Delete this reminder? This can't be undone.": "要删除这个提醒吗？删除后无法恢复。",
    "Reminder paused.": "提醒已暂停。",
    "Reminder on.": "提醒已开启。",
    "couldn't reach your reminders; try again": "连不上你的提醒，请再试一次。",
    "that reminder already finished; set a new time": "这个提醒已经结束了，请设一个新时间。",
    // deep research's card (research.js, research.py)
    "Research waiting to start": "研究等待开始",
    "Planning the research…": "正在规划研究…",
    "Writing the report…": "正在撰写报告…",
    "Research ready": "研究完成",
    "Research ready (partial)": "研究完成（部分）",
    "Research couldn't finish": "研究没能完成",
    "Research stopped: the server restarted": "研究已停止：服务器重启了",
    "Add your Anthropic API key in Settings to use research.": "请先在设置里添加你的 Anthropic API 密钥，才能使用研究功能。",
    "Research cancelled": "研究已取消",
    "Research is running on another server": "研究正在另一台服务器上进行",
    "Cancel this research? What it found so far isn't kept.": "要取消这个研究吗？已经找到的内容不会保留。",
    "Open report": "打开报告",
    "Save to Notion": "保存到 Notion",
    "Saving…": "正在保存…",
    "Saved to Notion": "已保存到 Notion",
    "Dismiss": "关闭",
    "Notion isn't connected": "还没有连接 Notion",
    "couldn't save to Notion; try again": "没能保存到 Notion，请再试一次。",
    // the Today screen (today.js); "Today", "Brief", "Reminders" and "Task" are above/below
    "Tomorrow": "明天",
    "Due": "待交",
    "Research": "研究",
    "To review": "要复习",
    "Ask Kwil": "问 Kwil",
    "Couldn't load Today. Check your connection.": "没能加载今天的内容，请检查网络连接。",
    "Retry": "重试",
    "Connect Google to see your calendar here.": "连接 Google 后，你的日历会显示在这里。",
    "Connect Google": "连接 Google",
    "Google needs you to sign in again.": "Google 需要你重新登录。",
    "Reconnect Google": "重新连接 Google",
    "Couldn't reach your calendar just now.": "暂时没能读取你的日历。",
    "Nothing on your calendar today.": "今天日历上没有安排。",
    "Nothing on your calendar tomorrow.": "明天日历上没有安排。",
    "Nothing due by tomorrow.": "明天之前没有要交的。",
    "Couldn't load what's due just now.": "暂时没能加载要交的作业。",
    "Notion needs you to sign in again.": "Notion 需要你重新登录。",
    "Open Settings": "打开设置",
    "No reminders today.": "今天没有提醒。",
    "Couldn't load this just now.": "暂时没能加载这一项。",
    "Overdue": "已逾期",
    "All day": "全天",
    "Researching": "正在研究",
    "Ready": "已完成",
    "Didn't finish": "没能完成",
    "Missed {n} times": "错过 {n} 次",
    // "Ask Kwil"'s questions ({time}, {title}, {n} are filled in after)
    "What's my {time} event today about?": "我今天 {time} 的活动是关于什么的？",
    "What's my {time} event tomorrow about?": "我明天 {time} 的活动是关于什么的？",
    "What's my all-day event today about?": "我今天的全天活动是关于什么的？",
    "What's my all-day event tomorrow about?": "我明天的全天活动是关于什么的？",
    "Tell me about “{title}” at {time} today.": "跟我说说今天 {time} 的“{title}”。",
    "Tell me about “{title}” at {time} tomorrow.": "跟我说说明天 {time} 的“{title}”。",
    "Tell me about “{title}” today.": "跟我说说今天的“{title}”。",
    "Tell me about “{title}” tomorrow.": "跟我说说明天的“{title}”。",
    "Quiz me on {title}.": "考考我：{title}。",
    "Quiz me on item {n} of my review list.": "就我复习清单上的第 {n} 项考考我。",
    "What's my event that continues today about?": "我今天还在进行的那个活动是关于什么的？",
    "Tell me about “{title}”, which continues today.": "跟我说说今天还在进行的“{title}”。",
    "until {time}": "到 {time}",
    "Too many requests; try again in a minute.": "请求太多了，请一分钟后再试。",
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
    "Pi only": "只用树莓派",
    "PC only": "只用电脑",
    "Cloud only": "只用云端",
    "Pi (tailnet)": "树莓派（tailnet）",
    "PC (tailnet)": "电脑（tailnet）",
    "Cloud": "云端",
    "Leave empty to use the address the server suggests.": "留空就使用服务器建议的地址。",
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
    "This app's address": "这个应用的地址",
    "Filter": "筛选",
    "Back to history": "返回历史记录",
    "This is the conversation you're in now.": "这就是你现在所在的对话。",
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
    "turn incognito off to continue a conversation": "关闭无痕模式才能继续这个对话。",
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
    "Welcome. A few one-time steps:": "欢迎。只需要做一次的几个步骤：",
    "Got it": "知道了",
    ["Add this app to your home screen: in Safari, tap the Share button (the square " +
      "with an arrow) at the bottom, then \"Add to Home Screen\", and open it from there."]:
      "把这个应用加到主屏幕：在 Safari 里点下面的分享按钮（带箭头的方框），" +
      "再点“添加到主屏幕”，以后从主屏幕打开。",
    ["Connect your own Google account so I can see your calendar and email: tap Settings, " +
      "then \"Google\" under Connections and \"Connect Google account\", and sign in with your own Google " +
      "account. If Google says it hasn't verified " +
      "this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
      "app was made by your family, not a company."]:
      "连接你的 Google 账户，我就能看你的日历和邮件：点“设置”，在“服务连接”里点“Google”，再点“连接 Google 账户”，" +
      "用你自己的 Google 账户登录。" +
      "如果 Google 说“此应用未经验证”，点“高级”，再点“转至…（不安全）”。这是正常的：" +
      "这个应用是你的家人做的，不是公司做的。",
    ["Connect your own Google account so I can see your calendar: tap Settings, " +
      "then \"Google\" under Connections and \"Connect Google account\", and sign in with your own Google " +
      "account. If Google says it hasn't verified " +
      "this app, tap \"Advanced\", then \"Go to … (unsafe)\"."]:
      "连接你的 Google 账户，我就能看你的日历：点“设置”，在“服务连接”里点“Google”，再点“连接 Google 账户”，" +
      "用你自己的 Google 账户登录。" +
      "如果 Google 说“此应用未经验证”，点“高级”，再点“转至…（不安全）”。",
  };
  const PATTERNS = [
    [/^(\d+) files? · (\d+) memor(?:y|ies)$/, "$1 个文件 · $2 条记忆"],  // projects.js's rows
    [/^New chat in ([^\n]{1,80})\.$/, "在“$1”里开始新对话。"],  // app.js
    [/^Text · ([\d.]+) (KB|MB)$/, "文本 · $1 $2"],
    [/^A project holds at most (\d+) files$/, "一个项目最多放 $1 个文件"],
    [/^A project file is at most ([\d.]+) MB$/, "一个项目文件最多 $1 MB"],
    [/^Your project files are at their limit \(([\d.]+) MB in all\): delete one first$/,
      "你的项目文件已经到上限了（一共 $1 MB）：请先删除一个"],
    [/^You have (\d+) projects: delete one first$/, "你已经有 $1 个项目了：请先删除一个"],
    [/^Studying ([^\n.!?]{1,80})$/, "正在学习 $1"],  // study.js's pill (a course name)
    [/^Researching… \((\d+)\/(\d+) searches\)$/, "正在研究…（已搜索 $1/$2 次）"],  // research.js's card
    [/^Missed (\d+) times$/, "错过 $1 次"],  // memory.js's study list
    [/^Source ([\d, ]{1,20})$/, "来源 $1"],  // sources.js's superscripts (aria-label)
    [/^Working: (.+)…$/, "正在处理：$1…"],
    [/^Error (\d+)$/, "错误 $1"],
    [/^Microphone unavailable: (.*)$/, "麦克风不可用：$1"],
    [/^Remove (.+)\? This deletes their conversations\.$/, "要移除$1吗？这会删除他们的对话记录。"],
    [/^Voice mode couldn't start: (.*)$/, "语音模式无法启动：$1"],
    [/^Actions for (.+)$/, "$1 的操作"],
    [/^No conversations match “(.+)”\.$/, "没有和“$1”相符的对话。"],
    [/^(\d+) messages$/, "$1 条消息"],
    [/^Continuing “(.+)”\. Go ahead\.$/, "继续“$1”，请说。"],
    [/^Branched into “(.+)”\. Go ahead\.$/, "已分支到“$1”，请说。"],
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
    // a beta tester's Settings and Connections row (family.js), the owner's People
    [/^\$(\d+\.\d\d) of \$(\d+(?:\.\d\d)?) starter credit left$/, "新手额度还剩 $$$1（共 $$$2）"],
    [/^\$(\d+\.\d\d) used$/, "已用 $$$1"],
    [/^Starter credit: \$(\d+\.\d\d) of \$(\d+(?:\.\d\d)?) used$/, "新手额度：已用 $$$1（共 $$$2）"],
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
    "Settings, then \"Google\" under Connections and \"Connect Google account\", and sign in with your own " +
    "Google account. If Google says it hasn't " +
    "verified this app, tap \"Advanced\", then \"Go to … (unsafe)\". That's expected: this " +
    "app was made by your family, not a company.";
  // a beta tester's (status "gmail": false): Calendar only, no mail wording
  const STEP_GOOGLE_CALENDAR = "Connect your own Google account so I can see your calendar: " +
    "tap Settings, then \"Google\" under Connections and \"Connect Google account\", and sign in with your " +
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
      lines.push(say("Welcome. A few one-time steps:"),
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

  // --- a beta tester's first-run checklist (family.js calls it on every
  // /api/state): what to try first, ticked by hand. The ticks and "Hide"
  // live in localStorage (private mode: nothing kept, still works). The
  // links are the server's https-only notion_template_url / beta_chat_url.
  const CHECK_TEXT = {
    title: "Things to try",
    connect: "Connect Canvas or Notion in Settings",
    template: "Notion template",
    ask: "Ask “What’s due this week?”",
    voice: "Try voice mode with the round button below",
    chat: "Join the beta testers' chat",
    chatLink: "Open the chat",
    hide: "Hide",
  };
  const CHECK_KEY = "betaChecklist";         // the ticked steps, a JSON list
  const CHECK_HIDDEN = "betaChecklistHidden";  // "1" after Hide

  function readTicks(win) {
    try {
      const list = JSON.parse(win.localStorage?.getItem(CHECK_KEY) || "[]");
      return Array.isArray(list) ? list.filter((k) => typeof k === "string") : [];
    } catch (e) { return []; }
  }
  function saveTicks(win, ticks) {
    try { win.localStorage?.setItem(CHECK_KEY, JSON.stringify(ticks)); } catch (e) { /* private */ }
  }
  const httpsLink = (v) => (typeof v === "string" && /^https:\/\/[^\s"<>]+$/.test(v) ? v : "");

  // the card hid with focus in it: give it to the message field, not <body>
  function focusText(doc) {
    const field = doc.getElementById("text");
    if (field && typeof field.focus === "function") field.focus();
  }

  function renderChecklist(win, doc, state) {
    const box = doc.getElementById("checklist");
    const list = doc.getElementById("checklist-items");
    if (!box || !list) return;
    const say = (text) => (isActive(doc) ? translate(text) : text);
    const beta = Boolean(state) && state.owner === false && state.api_key === "required";
    let hidden = false;
    try { hidden = win.localStorage?.getItem(CHECK_HIDDEN) === "1"; } catch (e) { /* private */ }
    const chat = beta ? httpsLink(state.beta_chat_url) : "";
    const steps = ["connect", "ask", "voice", ...(chat ? ["chat"] : [])];
    const ticks = readTicks(win);
    if (!beta || hidden || steps.every((k) => ticks.includes(k))) { box.hidden = true; return; }
    const template = httpsLink(state.notion_template_url);
    box.hidden = false;
    // the same steps and links as last time: only the ticks (a rebuild on
    // every /api/state would take focus from a checkbox)
    const sig = JSON.stringify([steps, template, chat]);
    if (list.dataset.sig === sig) {
      for (const input of list.querySelectorAll("input[data-step]")) {
        input.checked = ticks.includes(input.getAttribute("data-step"));
      }
      return;
    }
    list.dataset.sig = sig;
    list.textContent = "";
    for (const key of steps) {
      const li = doc.createElement("li");
      li.className = "check-item";
      const label = doc.createElement("label");
      label.className = "check-row";
      const box2 = doc.createElement("input");
      box2.type = "checkbox";
      box2.checked = ticks.includes(key);
      box2.setAttribute("data-step", key);
      const words = doc.createElement("span");
      words.textContent = say(CHECK_TEXT[key]);
      label.append(box2, words);
      li.appendChild(label);
      const href = key === "connect" ? template : key === "chat" ? chat : "";
      if (href) {
        const a = doc.createElement("a");
        a.className = "check-link";
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = say(key === "connect" ? CHECK_TEXT.template : CHECK_TEXT.chatLink);
        li.appendChild(a);
      }
      box2.addEventListener("change", () => {
        const now = readTicks(win).filter((k) => k !== key);
        if (box2.checked) now.push(key);
        saveTicks(win, now);
        if (steps.every((k) => now.includes(k))) { box.hidden = true; focusText(doc); }  // all done
      });
      list.appendChild(li);
    }
    const title = doc.getElementById("checklist-title");
    if (title) title.textContent = say(CHECK_TEXT.title);
    const hide = doc.getElementById("checklist-hide");
    if (hide && !hide.dataset.wired) {
      hide.dataset.wired = "1";
      hide.textContent = say(CHECK_TEXT.hide);
      hide.addEventListener("click", () => {
        try { win.localStorage?.setItem(CHECK_HIDDEN, "1"); } catch (e) { /* private */ }
        box.hidden = true;
        focusText(doc);
      });
    }
  }

  // the saved token lacks the Drive scope (a sign-in from before Drive)
  function lacksDrive(st) {
    return Array.isArray(st.missing) &&
      st.missing.some((s) => typeof s === "string" && s.endsWith("/auth/drive.readonly"));
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
      if (st.enabled && st.connected && lacksDrive(st)) {
        // Calendar and Gmail still work; the same Connect flow adds Drive
        return { state: "connected", status: "Connected", action: "Reconnect",
                 sub: "Reconnect to add Google Drive (read-only)" };
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
        } else if (hint && st.enabled && st.connected && lacksDrive(st)) {
          hint.hidden = false;
          hint.textContent = say("Reconnect to add Google Drive (read-only).");
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

  const api = { ZH, translate, translateNode, isActive, start, setupGoogle, renderChecklist,
                CHECK_TEXT };
  root.KwilI18n = api;
  if (typeof root.document !== "undefined") {
    const doc = root.document;
    if (isActive(doc)) start(root, doc);
    if (doc.documentElement && doc.documentElement.hasAttribute("data-google")) {
      api.google = setupGoogle(root, doc);
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
