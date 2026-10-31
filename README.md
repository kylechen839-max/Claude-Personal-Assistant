# Kwil

Kwil (say it like "quill") is an Echo-style voice assistant powered by
Claude, on the Anthropic API. Say the wake word, ask something, and it
answers out loud. It can read and change your Google Calendar, check and
update your Notion assignments, and remembers what you ask it to (your
instructions and saved memories, kept on your own machine; see
[Memory](#memory)).

**Wake word:** say **"Hey Claude"** for now. Kwil was called Hey Claude
until 2026-10-22, and the trained wake-word model (`keywords/hey_claude.onnx`)
is still the "Hey Claude" one; a "Hey Kwil" model ships once it is trained
(BLOCKERS.md). The phone app's voice mode needs no wake word; a "Hey Kwil"
(or "Hey Claude") at the start of what you say is left out of the request.

**Renamed from Hey Claude.** The package is `kwil` (`python -m kwil`). For
at least one stable release the old names keep working: `python -m
assistant`, `python -m assistant.host` / `.watch` / `.deploy` (what the old
tasks and units run) and `import assistant.X` give the same modules as
`kwil`, and a process still running the old code when the update lands (an
old `deploy.ps1`, web app or watchdog) finds the modules it imports later in
`assistant/` (one-line files pointing at `kwil`). Re-register the tasks when convenient (`install_windows_service.ps1`,
`install_family_tasks.ps1`, `install_watch_task.ps1` on the PC; `install_service.sh`
and `install_watch.sh` on a Pi): the new tasks live in `\Kwil\`, the new
units are `kwil.service` and `kwil-watch.timer`, and the old ones are
removed. Sign-ins, history, saved keys and the Render service and disk keep
their names, so nothing is lost and nobody is signed out.

Version 1.0.0: see CHANGELOG.md for what's included and PRIVACY.md for what
is stored and sent where. On your phone, it runs as a home-screen web app
("Install on iPhone" below).

```
mic -> openWakeWord wake word -> chime -> record until silence (VAD)
    -> faster-whisper (speech to text) -> Claude (+ tools, + memory)
    -> Kokoro / Piper / pyttsx3 (text to speech) -> speaker
```

## Quick start (Windows 11, PowerShell)

1. Install **Python 3.12** from python.org (tick "Add python.exe to PATH").
   3.10+ works, but the natural Kokoro voice needs 3.11+.
2. In the project folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   # If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   pip install -r requirements.txt
   copy .env.example .env
   notepad .env        # paste your keys
   ```
3. Get the keys and files:
   | What | Where | Goes in |
   |---|---|---|
   | Anthropic API key | console.anthropic.com | `.env` `ANTHROPIC_API_KEY` |
   | "Hey Claude" wake word model | already included: `keywords/hey_claude.onnx` | `config.toml` `[wake] keyword_path` |
   | Pinecone API key | app.pinecone.io | `.env` `PINECONE_API_KEY` |
   | Notion integration secret | notion.so/profile/integrations; share the Fall 2026 Assignments database with it | `.env` `NOTION_TOKEN` |
   | Google OAuth Desktop client | console.cloud.google.com: enable the Calendar API, add yourself as a test user, create a Desktop OAuth client | `credentials.json` |
4. Sign in to Google once: `python -m kwil.tools.calendar` (saves `token.json`).
5. Check the setup: `python -m kwil --check` (one line per key, file,
   audio device and model; never prints key values).
6. Run it:
   ```powershell
   python -m kwil              # always listening (wake word)
   python -m kwil --mode text  # type instead of talking
   ```
7. Optional: `pytest`. The live tests (real Notion, Google, Pinecone calls)
   read keys from the environment, not `.env`, so load it first:
   ```powershell
   Get-Content .env | % { if ($_ -match '^([A-Z_]+)=(.+)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
   pytest -rs
   ```

Any service without credentials is simply switched off (the app tells you
which), so you can start with only the Anthropic key in text mode.

## Modes and flags

| Command | What it does |
|---|---|
| `python -m kwil` | Wake-word mode (default): "Hey Claude", chime, speak, hear the reply |
| `--mode text` | Type, read replies |
| `--mode speak` | Type, replies spoken |
| `--mode voice` | Push-to-talk (Enter to start/stop) |
| `--audio-file f.wav` | Wake mode on a recording instead of the mic, then exit |
| `--tts print` | No audio output (prints what it would say); also `kokoro`, `piper`, `pyttsx3`, `elevenlabs`, `auto` |
| `--download-voice kokoro` | Download a voice at setup time, then exit (see "Voices") |
| `--timings` | Also print each turn's latency summary (always in the log) |
| `--keyword-path f.onnx` | Override the wake-word model |
| `--config other.toml` | Use a different settings file |
| `--check` | Check keys, files, audio devices and models, then exit |
| `--update` | Update now from the tested `stable` branch, then exit |
| `--mode web` | Private phone web app (see "Phone web app" below) |
| `--host`, `--port` | Web mode: where to listen (default 127.0.0.1:8765) |
| `--show-token` | Web mode: print the access token to the console once |
| `--role pi` | Which backend this is in the phone app's Pi -> PC -> cloud failover (`pi`, `pc` or `cloud`; see FAILOVER.md) |
| `--candidate` | Web mode: the go-live check's side-port copy (no history, no saved sign-ins, no background jobs, its own log); started by the watchdog, not by hand (see "Health checks and safe updates") |

**Follow-up mode:** after a reply, the assistant plays a soft, low blip
and keeps listening for 5 seconds without the wake word, so you can just
keep talking ("...and what about Friday?"). The follow-up is the next turn
of the same conversation. If you say nothing in that window it goes back to
waiting for "Hey Claude" and the conversation ends (it's summarized into
memory). "Goodbye" also ends it. Saying "Hey Claude" again during the window
is fine (it's stripped from what you said). The window opens only after the
reply has finished playing, and the mic is closed while the assistant
speaks, so it doesn't hear its own voice as a follow-up. Settings in
`config.toml` `[wake]`: `follow_up_seconds` (default 5; `0` turns follow-up
mode off, and then a conversation ends on "goodbye" or after
`[assistant] conversation_timeout`, 5 quiet minutes) and `follow_up_chime`
(`false` keeps the window but drops the blip; `chime = false` silences both
chimes). Ctrl+C quits. Deleting a calendar event always asks "yes or no?"
first; that answer goes to the question, not to the follow-up window.

**Interrupting a reply (barge-in):** say "Hey Claude" while it is talking
and it stops at once, plays the chime and listens to your new request, like
an Echo. "Hey Claude, stop" (or "never mind", "别说了") just stops it
without asking Claude anything. Setting `[wake] barge_in`:
- `"wake_word"` (default): only the wake word interrupts. Safe with
  ordinary speakers: the wake model doesn't fire on the assistant's voice.
- `"voice"`: just start talking. It listens while it speaks and stops when
  it hears `barge_in_ms` (400 ms) of speech that is `barge_in_echo_ratio`
  (2.5) times louder than its own voice coming back from the speakers
  (measured at the start of every reply, so it adapts to the volume). Best
  with headphones or an echo-cancelling USB speakerphone; with speakers next
  to the mic it may not hear you over itself (raise the volume of your voice,
  not the speaker), or may cut itself off (raise `barge_in_echo_ratio`).
  The echo is measured over the first 600 ms after the sound card's reported
  latency, so it allows for an echo that comes back up to about half a
  second later than reported. Bluetooth speakers often lag more than that
  (and more than they report): if a reply on a Bluetooth speaker cuts
  itself off within its first second, use wired speakers, headphones or
  `"wake_word"`; raising `barge_in_echo_ratio` doesn't fix a late echo.
- `"off"`: the mic stays closed while it speaks, as before (`false` works
  too; the value isn't case-sensitive).

Every voice can be stopped mid-reply: Kokoro, Piper, eSpeak and ElevenLabs
play through the app's own player; pyttsx3 is stopped from its word
callback on Windows (SAPI), and on Linux / the Pi each reply is first
rendered to a file and then played (eSpeak's own playback can't be cut
short), which adds a moment before it starts. The echo is measured after
the sound card's reported output + input latency (0.35 s if it reports
none), so a slow card doesn't make the reply's echo count as you talking.

What you said takes over as the next turn of the same conversation, and
Claude remembers only about the part of its answer you heard (estimated from
how long it played). Talking over a "yes or no?" question is always a no;
ask again if you meant yes. A reply that itself says "Claude" (reading out
an email, say) can't wake the assistant with its own voice: the wake word is
ignored for that reply. With `--audio-file` and barge-in on, the file is
read in real time while replies play, the way a mic would hear it.

**Timings:** every turn writes one line to `logs/assistant.log`, e.g.
`Turn timings from wake: end of speech +2.10 s, transcript +0.60 s, Claude
reply +1.80 s, first audio +0.10 s; wake to first audio 4.60 s (end of
speech to first audio 2.50 s)`. "End of speech" includes the time you spent
talking and the `silence_ms` pause that tells it you're done; the last
figure is the wait you notice. Run with `--timings` to see it on the
console too.

Things to try: "What's on my calendar tomorrow?", "What did we talk about yesterday?", "Move my dentist
appointment to Friday at 10", "What assignments are due this week?",
"Mark Homework 6 as done", "Delete the study group tomorrow",
"Make my dentist appointment red", "Add gym every Tuesday and Thursday at
7 AM", "When am I free for an hour tomorrow?", "Accept the project sync
invite",
"What's the weather this afternoon?", "Who won the Braves game last night?",
"What does this page say: emory.edu/...", "Open YouTube",
"Pull up the Canvas page for Classical Mechanics".

**Internet access:** web search and reading web pages run on Anthropic's
servers as part of the Claude request (no extra keys; usage is billed to
your Anthropic account per search). "Open ..." requests open pages in this
computer's default browser. Set `[web] location` in `config.toml` for local
answers like the weather, and `allowed_domains` / `blocked_domains` to limit
which sites are used. On a Raspberry Pi with no screen, set `browser = false`.

## Conversation history

Every conversation is saved: wake word and push-to-talk on the PC or Pi,
text mode, the phone app's push-to-talk and its hands-free voice mode. You
can search it and pick a conversation up later.

**By voice or text:** "Hey Claude, what did we talk about yesterday?",
"Find our conversation about the lab report", "Continue our conversation
about the lab report". Claude searches your history and asks *"Continue
'Lab report error bars' from Tuesday?"*; say yes and the old conversation
is loaded, so "and what about standard deviation?" is answered in context.
This works mid-conversation too (the current one is saved first).

**Asking about an earlier chat:** "What did we decide about my physics
schedule last week?", "Remember when we planned the lab report?" Kwil
searches your history (`search_conversations`: title, date, a short
summary and the best matching words for each hit), reads the best one
(`read_conversation`: up to 20 messages of up to 500 characters, the ones
around the matching words, or the summary and the last messages) and
answers from it, saying where it came from ("in your chat on Tuesday").
Reading doesn't switch conversations: you stay where you are. Archived
conversations are found too; deleted ones never, and incognito ones were
never saved. Like a resumed conversation, what was read counts as
untrusted, so changes later in that conversation ask first.

**On the phone:** the **History** tab in the bar at the bottom (on a wider
screen, **History** in the rail on the left, where a conversation opens
beside the list; **Ctrl+K**, or **Cmd+K** on a Mac, jumps to any screen or
past conversation) opens History:
a search bar (results update as you type, matching words highlighted),
filters (Pinned, Phone, Voice, PC, Typed, Archived), and conversations
grouped by Today / Yesterday / This week / Older with a source icon, time
and snippet. Swipe a row left for Pin / Rename / Delete, or long-press it
(or tap its "..." button) for all actions including Archive and Export.
Tap a conversation to read it; **Continue this conversation** shows it in
the chat, and the voice view continues it too (it uses the same
conversation). Delete always asks first.

Typing into an old conversation continues it. Opening one only reads it;
the chat is then tied to that conversation, and the first message you type
there goes on in it on the server, with its history as context. That holds
however long ago it ended: after the 5-minute idle timeout, after a voice
call ended it, or a day later. The reply is added to the same conversation
in History (no second copy is started). Like any resumed conversation, its
old text counts as untrusted, so changes (adding an event, say) ask first.
While Incognito is on nothing is tied (the server refuses it), and deleting
the conversation drops the tie and any message still queued for it. Saying
"goodbye" there ends that conversation and drops the tie too: the next
message starts a new one.

**Try again, edit, branch** (like ChatGPT and Claude). Under Kwil's last
reply a ↻ button asks for a new answer to the same message; the pencil on
your last message puts it back in the text box to change and send again.
The old reply (and, for an edit, your old message) is replaced: it is gone
from History, search and exports, on every synced backend. Both work only
between turns (not while Kwil answers or waits for a yes/no) and only when
that reply used nothing that may have changed something: if it added an
event, saved a note or turned on a light, Kwil says "That reply already
changed something, so I won't redo it. Ask again instead." (read-only
tools like reading your calendar or email are fine; a reply that switched
to another conversation, or checked the dev team's replies, counts as a
change). Stop before the new reply says anything keeps the old one. In a stored
conversation, long-press a message (or hover it with a mouse and tap its
"…") and pick **Branch from here**: a new conversation, "<title>
(branch)", gets the messages up to that one (at most 200, as text) and the
chat continues there; the original stays as it was. The branch is a copy:
deleting one of them leaves the other, so delete both to remove the words.
Not while Incognito is on.

**Incognito** in the settings menu (gear) stops saving for this phone
until you turn it off; the header shows an "Incognito" badge meanwhile. It
follows the phone's light or dark setting and works as a home-screen app on
iOS.

**From the command line:**

| Command | What it does |
|---|---|
| `python -m kwil --history list` | Newest 50 conversations (id, date, source, title); add `--source wake`, `--since 2026-10-01`, `--until 2026-10-14`, `--pinned` |
| `python -m kwil --history search lab report` | Ranked keyword search with the matching words highlighted (same filters) |
| `python -m kwil --history show ID` | The whole transcript |
| `python -m kwil --history export ID --format json` | Export as Markdown (`--format md`, default) or JSON, to the console; redirect with `> file.md` |
| `python -m kwil --history delete ID` | Delete (asks first; `--yes` skips the question) |
| `python -m kwil --mode text --continue ID` | Continue a conversation in text mode (`--continue last` for the most recent) |
| `python -m kwil --incognito` | Save nothing this session: no history and no memory summary |

**What is saved:** what you said and what Claude replied, as text, turn by
turn as it happens (a crash or power cut loses at most the turn in
progress), including spoken "yes/no" confirmations and replies you cut off
(marked "interrupted"). For tool use only the tool name, a few short
arguments and whether it worked are kept (e.g. "list calendar events, day =
tomorrow, ok"), never the results, so calendar details or web pages are not
copied into history. API keys and tokens are masked before saving. When a
conversation ends, a fast model (`[history] title_model`, Haiku) writes a
title of up to six words and a one or two sentence summary in the
background, so replies never wait for it; if the API is unreachable, the
first thing you said becomes the title.

**Search:** keyword search uses SQLite full-text search (FTS5) over messages,
titles and summaries, ranked by relevance, with prefix matching (typing
"chem" finds "chemistry"); if your Python's SQLite lacks FTS5 it falls back
to simpler matching. A conversation whose title or summary matches comes
before ones where the words were only said in passing. No Pinecone is
needed (`kwil/recall.py`):

- **By meaning, on your machine** (optional): `[memory] embeddings =
  "fastembed"` also keeps one small vector per conversation, made from its
  title and summary, in the same history file, so "physics revision
  timetable" finds "Mechanics exam study plan". Install it with
  `pip install fastembed`, then download the model once with
  `python -m kwil.recall --download` (it is read only from
  `[memory] embedding_dir` afterwards; nothing is downloaded during a
  conversation). Keyword and meaning hits are merged by reciprocal rank
  fusion. Existing conversations are indexed in the background at startup
  and on the first search (500 per run, resuming where it stopped; a
  search never waits for it); `python -m kwil.recall --backfill` does it
  all at once. Deleting a conversation deletes its vector with it.
  Meaning search starts only once the model is downloaded: until then (or
  if fastembed is missing or fails) search is keyword-only, and Kwil tries
  the model again after 10 minutes, or at once when the model folder
  changes (e.g. right after `--download`), without a restart.
- **With `[memory] backend = "pinecone"`:** keyword search plus the
  Pinecone conversation summaries, which carry the conversation id, so a
  semantic match ("that thing about uncertainty in graphs") also links
  back to the full conversation.

Searching 5,000 conversations takes well under 100 ms.

**Where it lives:** one SQLite file, `data/history.sqlite` (gitignored) on
the PC's disk or the Pi's SD card; on the Render deploy the `HISTORY_PATH`
environment variable points it at the persistent disk
(`/data/history.sqlite`). Settings in `config.toml` `[history]`:

| Setting | Default | What it does |
|---|---|---|
| `[history] enabled` | true | Save and search conversations |
| `[history] path` | "data/history.sqlite" | Where the database file goes (`HISTORY_PATH` overrides) |
| `[history] retention_days` | 0 | Delete unpinned conversations older than this; 0 = keep forever |
| `[history] max_mb` | 500 | Size guard: above this, the oldest unpinned conversations are deleted |
| `[history] incognito` | false | Never save (same as always passing `--incognito`) |
| `[history] title_model` | "claude-haiku-5-5" | Model for titles and summaries |

Deleting a conversation also deletes its memory summary in Pinecone.
Privacy details are in SECURITY.md.

**How it's stored:** every change (a message, a title, a pin, a delete) is
an event in the history sync log (failover's event log, in the same file),
and the searchable tables are built from those events. With the failover
setup the Pi, the PC and the cloud exchange events, so all three show the
same history, and a delete anywhere deletes everywhere (the deleted text is
wiped from the log too). Retention and the size guard delete the same way,
so they also apply on every backend.

## Settings

`config.toml` holds all non-secret settings; anything left out uses the
default in `kwil/config.py`. `CLAUDE_MODEL` in the environment
overrides `[assistant] model` (default `claude-sonnet-5-5`). Logs go to
`logs/assistant.log` (rotating); the console only shows warnings. The ones
you are most likely to tune:

| Setting | Default | What it does |
|---|---|---|
| `[wake] sensitivity` | 0.5 | Higher = fewer missed wakes, more false wakes |
| `[wake] silence_ms` | 800 | Pause that ends your command |
| `[wake] vad_min_threshold` | 0.015 | Minimum loudness counted as speech; raise in a noisy room |
| `[wake] no_speech_timeout` | 5 | Seconds to wait for a command after the wake word |
| `[wake] follow_up_seconds` | 5 | Follow-up window after a reply; 0 = off |
| `[wake] barge_in` | "wake_word" | Interrupt a reply: "wake_word", "voice" (just talk; headphones) or "off" |
| `[wake] mic_device` | "" | Mic index (0 included) or part of its name, for wake and push-to-talk; `--check` lists them and shows the one that will open |
| `[tts] engine` | "auto" | "auto", "kokoro", "piper", "pyttsx3", "espeak", "elevenlabs" or "print"; `[tts] fallback` is the last resort (pyttsx3, or espeak on a server) |
| `[tts] kokoro_voice` | "af_heart" | Kokoro voice (see "Voices") |
| `[tts] speed` | 1.0 | Speaking speed for Kokoro and ElevenLabs |
| `[tts] picker` | true | Phone app: everyone picks their own reply voice (see "Voices") |
| `[stt] model` | "small.en" | faster-whisper model (about 480 MB, downloaded on first run); "base.en" or "tiny.en" are faster and mishear more. A Pi gets "base.en" (`install_pi.sh` writes it to `config.local.toml`), the cloud server too |
| `[stt] beam_size` | 5 | Beam search width; 1 = greedy (faster, mishears more); 0 = the default |
| `[stt] initial_prompt` | "auto" | Words Whisper should expect: "auto" = the app's vocabulary (calendar, assignment, Notion, reschedule, ...; the Mandarin prompt in Chinese), "" = none, or your own text |
| `[stt] hotwords` | "" | Extra words to favor, such as names: "Kyle, Kai" |
| `[stt] engine` | "whisper" | "whisper" (local), or paid cloud: "openai" or "deepgram" (see [Cloud speech-to-text](#cloud-speech-to-text-optional-paid)) |
| `[stt] cloud_model` | "" | "" = gpt-4o-transcribe (openai) or nova-3 (deepgram) |
| `[stt] cloud_timeout` | 10 | Seconds before local Whisper takes over (1-60) |
| `[assistant] timezone` | "" | IANA name, e.g. "America/New_York"; "" = system |
| `[update] enabled` | false | Home host (PC tasks, Pi service): follow the tested, signed `stable` branch; set it in `config.local.toml`, see [Automatic updates](#automatic-updates) |
| `[assistant] api_timeout` | 30 | Seconds before a Claude request gives up |
| `[assistant] max_tokens` | 1500 | Ceiling for a reply, thinking included (only what's used is billed): fast-model turns and turns without tools |
| `[assistant] max_tokens_tools` | 4000 | Ceiling for `model` turns that offer tools, room to plan a schedule over several tool rounds (at least `max_tokens`) |
| `[assistant] max_history` | 40 | How much of the conversation Claude still sees, in messages of plain chat: the newest `max_history / 2` turns, each with all its tool rounds; past that the older half is dropped, whole turns at a time |
| `[assistant] max_history_chars` | 300000 | The conversation's size budget (about 100k tokens): past it the oldest whole turns are dropped until it is under half, the newest turn always stays. If Claude still answers "prompt is too long", the older half of the earlier turns goes and the request is sent once more; if that fails too they come back, and a turn with a photo or file isn't shortened at all (the file is the likely cause). 0 = no budget |
| `[assistant] thinking_turns` | 3 | How many earlier turns' thinking (Claude's reasoning) is sent again, at least; it moves in steps of this many so the prompt cache holds |
| `[assistant] fast_model` | "claude-haiku-5-5" | Cheaper model for short, simple questions; `CLAUDE_FAST_MODEL` overrides it |
| `[assistant] router` | "auto" | "auto": simple turns go to `fast_model`, changes, lookups, reasoning, attachments and untrusted content stay on `model`; "off" (or `CLAUDE_ROUTER=off`): always `model` |
| `[assistant] usage_file` | "data/usage.json" | Monthly API token totals and a rough cost estimate (the owner's phone app gets `month_usd`); "" = not kept |
| `[spotify] enabled` | false | Spotify playback by voice (Premium); see [Spotify by voice](#spotify-by-voice) |
| `[memory] enabled` | true | Kwil's memory: your instructions and saved memories (see [Memory](#memory)) |
| `[memory] backend` | "local" | "local": memory on this machine only, Pinecone is never called; "pinecone": also recall notes before each reply and save conversation summaries in Pinecone (needs `PINECONE_API_KEY`) |
| `[memory] local_path` | "data/memory.sqlite" | Where the owner's instructions and memories are kept (`/data/memory.sqlite` on the cloud server); each family member's are in their own folder |

**Cost: model routing and prompt caching.** Each turn picks its model
before the request, with no extra API call (`kwil/router.py`): a short,
simple question ("what time is it") goes to `fast_model` (Claude Haiku).
Words that usually mean a change (add, move, book, set, turn off, lock,
pause, forget, save, ...), a web lookup or reasoning, a long request, an
attachment, or untrusted text (web, email, invites) earlier in the
conversation send it to `model`. Words can miss a phrasing, so there is a
second check: if Haiku answers with any tool call that isn't read-only
(the list is `READ_ONLY_TOOLS` in `router.py`: calendar, assignment, email,
notes and home *reads*), that answer is dropped before anything runs and
`model` answers the whole turn again from your message (any read Haiku made
earlier in the turn is dropped too and run again if needed; its signed
thinking can't be sent to another model). Every change is therefore made by
`model` (words Haiku said before its tool call may already have been
spoken). Once a
conversation needed `model` it stays there until it ends. Confirmations are
the same on either model. The tools and system prompt are the same bytes on
every request and are cached by the API (5 minutes); the date and recalled
notes travel with each of your messages instead, and the weekly review
offer with the first one only. Within a conversation Claude also gets its
own earlier reasoning back (its thinking from the last few turns,
`thinking_turns`), so "why did you block only 3:30 to 4?" has an answer.
Thinking from before a resume or a model change isn't sent again (the API
accepts thinking only from the model and conversation that wrote it);
later turns' thinking is kept. A reply cut off by `max_tokens` never sends
its thinking again. Each Claude API call's token counts (chat,
summaries, history titles, tutor, lecture notes: input, output, cache
read/write, web searches) are logged at INFO in `logs/assistant.log`,
without any text.

### Speech recognition

Every way of talking to it (wake word, push-to-talk, the phone app's
microphone button and hands-free voice) goes through the same Whisper
settings in `[stt]`. On a PC the default is `small.en` (int8, about 480 MB,
downloaded on first run; `python -m kwil --check` says whether it is
cached) with beam search 5 and a short prompt of the words the app hears
most (calendar, assignment, Notion, reschedule, remind me, Canvas, exam,
quiz...). Before, it was `base.en` with greedy decoding and no prompt
(a bigger model and beam search are Whisper's two main accuracy levers;
the gain on your own voice isn't measured yet, see MANUAL_TEST.md). It
costs time: about two to three times `base.en`'s per utterance on a CPU
(an estimate), so a slow PC can go back with `model = "base.en"`. If it
still gets a name wrong, add it to `[stt] hotwords`
(e.g. `hotwords = "Kyle, Kai"`, best in `config.local.toml`, under its
`[stt]` line if it has one: a table may appear only once). On near-silent
audio Whisper sometimes "hears" its own prompt back; that is treated as
nothing said. Your words are written to the log only at DEBUG.

#### Cloud speech-to-text (optional, paid)

For the best accuracy you can let OpenAI (`gpt-4o-transcribe`) or Deepgram
(`nova-3`) transcribe instead: put `OPENAI_API_KEY=...` or
`DEEPGRAM_API_KEY=...` in `.env` and set `[stt] engine = "openai"` or
`"deepgram"` (on Render: the `STT_ENGINE` setting and the key). Every
recording is then sent to that company's servers (with the vocabulary and
hotwords, as their prompt or keyterms) and costs money per minute; invited
people's speech too, within their `stt_seconds_per_day`. Each request has a
timeout (`cloud_timeout`, 10 s); if it fails (network, quota, a bad key) that
recording is transcribed by local Whisper, and the cloud is skipped for a
minute. Recordings over five minutes stay local. Without the key local
Whisper is used. The key, the audio and the replies are never logged.

### Voices

With `[tts] engine = "auto"` (the default) the assistant uses the best voice
you have downloaded, and logs one line saying which and why:

1. **Kokoro** (most natural; [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M),
   Apache-2.0, run by the `kokoro-onnx` package). The default voice was
   picked to be close to the calm, warm feel of the Claude app's voice
   conversations (the app's own voices aren't available outside it). About
   120 MB: the int8 model (88 MB) and the voice pack (28 MB). Needs Python
   3.11+ (it relies on onnxruntime 1.25+, which has no 3.10 build); on 3.10
   "auto" uses Piper.
2. **Piper** (fast neural voice, the best choice on a Raspberry Pi; about 60 MB).
3. **pyttsx3** (the OS voice: SAPI5 on Windows, eSpeak on Linux), always there.

Download them once, at setup (never while answering). Each Kokoro file's
SHA-256 is checked, and a bad download is deleted:

```powershell
python -m kwil --download-voice kokoro                # into models/kokoro/
python -m kwil --download-voice en_US-lessac-medium   # Piper, into voices/
python -m kwil.tts --voice-file voices/en_US-lessac-medium.onnx --say "Hello Kyle"
```

`models/` and `voices/` are not committed. On a Raspberry Pi, "auto"
prefers Piper even if Kokoro is there, because Kokoro is several times
slower on ARM (PROGRESS.md has the numbers). `--check` shows which voice
is in use.

**Switching voices.** In `config.toml`:

```toml
[tts]
engine = "auto"            # or "kokoro" / "piper" / "pyttsx3" to force one
kokoro_voice = "af_heart"  # the default
speed = 1.0                # 0.9 = a little slower and calmer
```

| Kokoro voice | Sounds like |
|---|---|
| `af_heart` (default) | American, female: warm, calm, the most natural of the set |
| `af_bella` | American, female: brighter and more animated |
| `bf_emma` | British, female: soft and composed |
| `am_michael` | American, male: relaxed, low |
| `af_nicole`, `af_sarah`, `bm_george`, `bm_lewis`, ... | the rest of the voice pack (`am_`/`bm_` = male, `bf_` = British female) |

A Kokoro voice name that doesn't exist logs a warning and uses `af_heart`.
If a forced engine's files are missing, it falls back down the list (Kokoro
-> Piper -> pyttsx3) with a warning instead of crashing. For Piper, set
`piper_voice` to another voice from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) (e.g.
`en_US-amy-medium`). `[tts] rate` and `voice` apply to pyttsx3 only. `--tts
kokoro` on the command line tries an engine for one run.

**Optional cloud voice: ElevenLabs (paid).** Put `ELEVENLABS_API_KEY=...` in
`.env` and set `[tts] engine = "elevenlabs"` (optionally `elevenlabs_voice`,
a voice ID from your ElevenLabs library; the default is the premade
"Rachel", and `elevenlabs_model` is `eleven_flash_v2_5`, its fastest). Every
sentence goes to ElevenLabs' servers and counts against your plan. Each one
is streamed with a timeout; if the request fails (network, quota, a bad
key) that sentence is spoken by the local voice above, and the cloud is
skipped for a minute. Without the key the local voice is used. The key is
never logged.

**Pick your voice in the phone app.** The settings menu (gear) has **Reply
voice**: a list of the voices this server really has, in your language,
with **Preview** to hear a short sample. It offers the Piper voices in
`[tts] voices_dir` (`voices/`), the Kokoro voices if Kokoro is set up (not
on the cloud server, which has too little memory for it), the system voice
(pyttsx3, or espeak-ng on a server) and, only with `ELEVENLABS_API_KEY`
set, your ElevenLabs voices (listed from your account, refreshed hourly,
tagged **paid**; you can also type a voice ID). Chinese speakers see the
Chinese voices (e.g. `zh_CN-huayan-medium`: `python -m kwil
--download-voice zh_CN-huayan-medium`). The choice is per person: yours is
saved on that server in `[tts] voice_file` (`data/voice_choice.json`;
`/data/` on the cloud, `data/mom/` for `config.mom.toml`), each family
member's in their own folder (deleted when you remove them), and it is used
from the next reply on, in push-to-talk and voice conversations alike.
Family members can't pick ElevenLabs voices (your bill) unless you set
`[tts] family_paid_voices = true`, and even then only family-tier members
can; beta testers never see or get them, even when ElevenLabs is the server's
default `[tts] engine` (they hear its local fallback). A voice that has gone away (file deleted,
key removed) falls back to the default with one log line. "Use the phone's
voice" in the same menu still wins over all of these. `[tts] picker =
false` turns the picker off.

**Sounds.** Voice mode on the phone plays three soft cues: a gentle two-note
rise when it starts listening, a very quiet low note when your turn has
been heard ("thinking"), and a falling pair when the conversation ends. The
desktop wake chime uses the same listening sound. They are original
sine-wave sounds generated by `kwil/earcons.py` (`python -m
kwil.earcons` rewrites `kwil/web/earcons/*.wav`).

## Phone web app

`python -m kwil --mode web` runs the same assistant (Calendar, Notion,
memory, web search, delete confirmations) as a private web page for your
phone. You talk into the phone's own mic; the PC transcribes with Whisper,
asks Claude, and sends back the reply as text and audio (in the voice from
[Voices](#voices); or tick "Use the phone's voice" in the page's settings). The composer is one row: **+**
(photos, files, Think harder, Study mode), the text field (it grows to
about 6 lines, then scrolls), the mic, and on the right **Voice
conversation** while the field is empty or **Send** once there is
something to send. Hold the mic to talk (or tap once to start and again to
stop), or type; Enter sends and Shift+Enter starts a new line. You can keep
typing while Kwil is answering (Stop takes the mic's place): Send then queues the message (up to 3, each with an × to
remove it) and it goes as soon as the reply is done; **Stop** ends the
reply early and sends the queue. ↻ under the last reply tries again and
the pencil edits your last message (see
[Conversation history](#conversation-history)). When Claude wants to delete something, Yes/No
buttons appear; saying "yes" into the mic works too. Each phone/browser
gets its own conversation; after `[assistant] conversation_timeout` (5
minutes) of quiet it ends and is summarized to memory, like the voice loop.
Opening pages in the PC's browser is off in this mode.

**It is private by design.** The server listens on 127.0.0.1 only, so nothing
outside the PC can reach it. Every request needs a session cookie that you
get only by entering the access token once (`WEB_TOKEN`, a 43-character
random value that is created in `.env` the first time you start web mode and
is never written to the log). Wrong tokens are rate-limited; requests from
other sites are refused; the page sends strict security headers.

The phone's mic only works over HTTPS, so reach the PC through
[Tailscale](https://tailscale.com/download), which gives it a private
HTTPS address that only your own devices can open:

1. Install Tailscale on the PC and on the phone, and sign in to the same
   account on both. In the Tailscale admin console (DNS page), make sure
   MagicDNS and HTTPS certificates are enabled.
2. On the PC, start the web app and print the token once:
   ```powershell
   python -m kwil --mode web --show-token
   ```
3. In a second PowerShell window, publish it to your tailnet (not the
   internet; that would be `tailscale funnel`, don't use it):
   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   tailscale serve status    # shows https://<pc-name>.<tailnet>.ts.net
   ```
4. On the phone (Tailscale on), open that `https://...ts.net` address, paste
   the token, and allow the microphone. Add it to the home screen (see
   below; on Android, Chrome menu -> Add to Home screen).

### Attach photos and files

The **+** left of the text field opens a sheet: **Take photo** (the camera),
**Photo library**, or **Choose file** (PDF or a plain text / Markdown file).
What you pick shows as chips above the field, each with an **×** to remove
it, and goes to Claude with your next typed message or push-to-talk
recording ("what does this say?"); a photo on its own can be sent too. Up to
4 per message (the **+** greys out at 4). The phone shrinks photos to 1600
px first; the server checks every file by its first bytes (not its name):
JPEG, PNG, WebP or GIF photos up to `[tutor] max_image_mb` (8), re-encoded
without EXIF/location; PDFs and UTF-8 text files up to `[webapp]
max_attachment_mb` (10); a PDF may have at most 100 pages (Claude's limit),
and a file Claude still finds too long gets "That file is too long for
Claude" (a password-protected or broken PDF: "Claude couldn't read that
file.") instead of a generic error. The whole message must also fit in `[webapp]
max_upload_mb` (10; 4 on the cloud server, so send big PDFs through the PC).
The files are sent to Anthropic with the message and kept only in memory for
the conversation; saved history gets a marker such as `[photo]` or
`[file: syllabus.pdf]`, never the file. Because an attached file is written
by someone else, Claude asks before changing anything (calendar, Notion)
for the rest of that conversation, as after reading a web page or an email.
Family members can attach files too; theirs go only to their own
conversation. The hands-free voice view doesn't take attachments.

### Think harder, and where an answer came from

**Think harder** is for one message: tap **+**, then **Think harder** (a
"Think harder is on" chip shows above the field; tap it to turn it off; the
next typed or push-to-talk message takes it), or press and hold Send, and that message goes to the main model
(`[assistant] model`, even when the router would have picked the fast one)
with more room (`think_max_tokens`, 16000) and thinking asked for explicitly
(adaptive thinking at `think_effort`, "xhigh"). The status line says
"Thinking harder…". The next message is ordinary again; a message queued
while Kwil answers keeps its own choice, and a yes/no answer never has one.
Saying or typing "think hard about…" (or "仔细想想…") at the start of a message
does the same, also by voice. Each person (you, and each invited person, on
the chat's own model) gets `think_per_day` (30) a day; past that the
message is answered the usual way and the chat says so. Counts reset when
Kwil restarts.

**Sources.** When Kwil searched the web, the reply shows small numbered
superscripts and, under it, the pages it cited (title and site; tap to open
in a new tab). When it answered from your own data (a past chat it read, a
project file, a Canvas course file, a file you attached) it says so in
words ("from your chat on Tuesday") and the reply lists "Sources: Chat on
Tue 28 Oct · syllabus.pdf"; tap a chat to open it, a project file to open
its project. An attached file is only named ("plan.pdf (attached)"): it
isn't kept, so there's nothing to open. Sources are saved
with the reply, so History, transcripts and exports show them; deleting,
trying again or editing removes them with the reply. Spoken replies never
read a web address.

### Install on iPhone

1. Install the Tailscale app on the iPhone and sign in to the same account
   as the PC (leave it connected).
2. Open the `https://<pc-name>.<tailnet>.ts.net` address in **Safari**.
3. Share -> **Add to Home Screen** -> Add. A "Claude" icon appears.
4. Open it **from the icon** and enter the token once. The home-screen app
   keeps its own cookies, separate from Safari, so sign in inside the app
   even if you already did in Safari. It stays signed in for 90 days, renewed
   each time you use it.
5. Tap the mic and allow the microphone. If you said no by mistake: Settings
   -> Safari -> Microphone -> Allow (or in Safari, aA -> Website Settings ->
   Microphone), then reopen the app.

Replies play through the phone's speaker even with the ringer switch on
silent. The app shell (page, script, icons) is cached so it opens quickly;
conversations and audio are never cached.

`tailscale serve reset` stops publishing it. "Sign out" in Settings (the gear)
revokes that phone's cookie on the server; "Sign out everywhere" (e.g. after
losing a phone) revokes every browser's (with failover, every phone's access
token on every backend too, see FAILOVER.md). Signed-in phones are listed in
`[webapp] sessions_file` (`data/web_sessions.json`, ids only, no token) so
they stay signed in across restarts; on a host without a persistent disk a
redeploy signs everyone out. Scripts using the token as a Bearer token are
cut off only by changing it: delete `WEB_TOKEN` from `.env` and restart, and
a new one is created. Settings live in `config.toml` `[webapp]`: `port`
(8765), `confirm_timeout` (seconds to wait for a yes, 120), `max_upload_mb`
(10), `max_attachment_mb` (10, per attached PDF or text file),
`max_audio_seconds` (60), and `allowed_origins` (only needed if a proxy
other than Tailscale rewrites the Host header). Keep `host` at "127.0.0.1";
`--check` reports whether the token is set. On the Pi it is the same: install
Tailscale there and run the same two commands.

**On a public server** the ready-made way is the Docker image and Render
Blueprint in the next section (it adds an auth wall in front of every route
and escalating lockouts). By hand, behind an HTTPS proxy: set `WEB_TOKEN`
as a secret in the host's environment (the app refuses to listen on anything
but 127.0.0.1 without it), set `TRUSTED_PROXY=1` (or `[webapp]
trusted_proxy`) so it uses the proxy's X-Forwarded-For/Proto headers (it
also takes the proxy's address instead, e.g. `TRUSTED_PROXY=127.0.0.1` for
`tailscale serve` on the same PC: the headers then count only from there), and
run `python -m kwil --mode web --host 0.0.0.0` (it reads `PORT` from the
environment). With a trusted proxy, plain-http requests are redirected to
https and HSTS is sent. Five wrong tokens from one address lock that address
out of further guesses for 15 minutes; past 20 wrong tokens from everyone
together, each further wrong one is answered a second later. The right token
always works, so nobody can lock you out by guessing. `GET /healthz` answers `{"ok":
true}` without signing in. Scripts can send `Authorization: Bearer
<WEB_TOKEN>` instead of the cookie.

### Voice conversation (hands-free)

Tap **Voice conversation** (the round waveform button at the right of the
composer while the field is empty) and just talk, like voice
mode in the Claude apps. A full-screen orb shows what it is doing: it moves
with your voice while **listening**, pulses while **thinking**, and moves with
the reply while **speaking**. When you stop talking for `[voice]
silence_ms` (700 ms) it sends what you said, and the answer starts playing
as soon as its first sentence is ready (the rest is still being written).
Talk over it and it stops at once (barge-in, after `barge_in_ms` = 250 ms of
speech); what you say next is the new question, and Claude remembers only
the part of its answer you actually heard. Barge-in only counts once the
answer has started playing: anything you say while it is still thinking
(a last word, "and on Friday") never cancels it; it is asked as the next
question once the answer has played (but never while a yes/no question is
open: then only the answer after the question counts). Talking in the
short pause between two of its sentences doesn't count as a barge-in
either. The app's own sounds can't start or stop a turn. The conversation keeps going
turn after turn until you tap **End**, say "goodbye", or stay quiet for
`idle_timeout` (2 minutes); then it is summarized to memory like the voice
loop. Deletes still ask out loud. Let the question finish, then say
"yes" (or "yeah", "yep", "ya", "sure", "okay", "do it", "go ahead",
"please", "right", "好", "可以"...; case and punctuation don't matter). "No",
"cancel", "stop" or talking over the question means no ("OK, I won't delete
it"). Anything else (it misheard you, or you said something unrelated) gets
one "Sorry, I didn't catch that. Please say yes or no."; a second unclear
answer cancels ("I still didn't catch that, so I left it"). The answer is
never sent to Claude as a new request.

- Buttons: **End**, **Mute** (stops listening; the reply still plays),
  **CC** (captions of what it heard and what it is saying, remembered on
  this phone), the camera (see **Camera** below) and a stopwatch for the latency panel (also on by default with
  `debug_panel = true`).
- Hearing itself: the page asks the browser for echo cancellation, noise
  suppression and auto gain, and needs a more confident detection while a
  reply plays. If it still interrupts itself on the speaker, raise
  `vad_threshold` (e.g. 0.7) or use earphones; in a car whose speakers it
  keeps hearing, `barge_in = "off"` makes replies play to the end (the mic
  is ignored while one plays, and listens again right after).
- Follow-ups: when a reply has finished playing it plays a soft low note and
  shows **Still listening…** (还在听…) with the mic meter lit for
  `follow_up_seconds` (5; `0` = no cue). Just ask the next thing. If you say
  nothing the label quietly goes back to **Listening**; the conversation
  stays open until `idle_timeout`. There is no cue after a yes/no question
  (it is waiting for your answer) or after you interrupted a reply.
- Voice detection runs on the phone: the Silero VAD neural model in
  WebAssembly (onnxruntime-web, served by the app itself from
  `kwil/web/vad/`, about 14 MB, cached after the first load). If it
  can't load, or with `vad = "energy"`, a simpler loudness-based detector is
  used. The latency panel says which one is active.
- Backgrounding the app, a phone call or Siri pauses the conversation (the
  current answer is cancelled); tap **Tap to continue** to go on. The screen
  stays awake while it is open, where the browser allows it.
- The connection is a WebSocket on the same address and sign-in as the rest
  of the page (Tailscale or the hosted setup work unchanged). With
  `--timings`, each turn's latency line is printed on the PC's console too.

**On an iPhone** (Safari, or the app added to the home screen):
1. Open the page, tap **Voice conversation**, and tap **Allow** when iOS
   asks for the microphone. That one tap also unlocks the speaker, so keep
   the ringer switch off silent mode if you hear nothing.
2. Talk normally and stop: the orb pulses while it thinks, then the answer
   plays from the loudspeaker (or AirPods). The orb moving with the reply
   means it is speaking; just talk to interrupt it.
3. **Mute** keeps the conversation open without listening (handy while
   someone else talks); **End** closes it.
4. If it says the microphone is unavailable, or never asks: in Safari tap
   **aA** -> **Website Settings** -> **Microphone** -> **Allow**; or go to
   iPhone **Settings -> Apps -> Safari -> Microphone** -> Ask or Allow. For
   the home-screen app, remove it and add it again after allowing. The page
   must be opened over `https://` (the Tailscale or hosted address), never
   `http://`.
5. Locking the phone, switching apps, a call or Siri pauses it; tap **Tap to
   continue** when you're back.

**Camera** (show Kwil what you're looking at): tap the camera button next
to **CC**. The back camera's live picture fills the screen, a red **Camera
on** pill shows while it is live, and the orb moves to a corner (it still
shows listening and speaking). While the camera is on, each question you
ask carries one picture of what the camera sees at the moment you stop
talking, and the answer is spoken as usual ("what does this sign say?",
"which of these is the 10 mm bolt?"). **Send this frame** freezes the
picture; that frozen frame goes with your next question instead (tap it
again to unfreeze). An answer to a yes/no question never carries a
picture. Tap the camera button again to turn it off; backgrounding the app
or ending the conversation turns it off too.
- The phone shrinks each frame to `[voice] camera_max_side` (1024 px) on
  its long side and JPEG-encodes it under `[voice] camera_max_kb` (400 KB);
  the server checks the bytes, re-encodes it (dropping EXIF: location,
  camera serial) and refuses anything bigger. `[voice] camera = false`
  (or a cap of 0) hides the button and the server refuses frames.
- Only the newest `[voice] camera_keep_frames` (1) frames stay with the
  conversation; older ones become "(earlier photo, no longer attached)", so
  a long camera conversation doesn't grow. Photos from the **+** are not
  affected.
- Frames are never saved, even with History on: the history keeps
  "[photo]" in their place. A picture counts as someone else's text (a
  sign, a letter), so after one Kwil asks before changing anything, as
  after an attached photo.
- iPhone: the camera is asked for when you tap the button (the home-screen
  app asks again each time it is launched). If you tapped **Don't Allow**,
  close Kwil completely (swipe it away) and open it again, then allow; or
  **Settings -> Apps -> Safari -> Camera** -> Ask. The microphone keeps
  listening while the camera is on.
- Invited people can use it too; each picture turn counts against their
  own limits and key like any other turn.

Other `[voice]` settings: `min_utterance_ms` (300, shorter sounds are
ignored), `max_utterance_s` (30), `max_session_minutes` (30) and
`utterances_per_minute` (30); a yes/no question waits `[webapp]
confirm_timeout`. Voice and push-to-talk share this phone's conversation.
Replies use the voice from [Voices](#voices) (Kokoro once downloaded),
rendered sentence by sentence so the first one plays while the rest are
made; the model is warmed up when the server starts.

## A Chinese instance for someone else (`config.mom.toml`)

A second, separate copy of the phone app in Mandarin Chinese, for a family
member to try. It runs on the same PC next to yours, on its own port, and
uses **none of your connections**: no Notion, no Pinecone memory or notes,
no dev team, its own history (`data/mom/`), sign-ins and log
(`logs/mom.log`). She connects her own Google Calendar and Gmail (read and
drafts only; nothing is ever sent) from her phone with a menu button.

`[assistant] language = "zh"` makes Claude answer in Simplified Chinese,
Whisper listen for Mandarin (the multilingual `small` model; `.en` models
can't hear Chinese), yes/no work in Chinese, and the page show in Chinese.
A Chinese yes is the whole answer, from a short list (是 / 是的 / 对 / 对的 /
好 / 好的 / 好啊 / 好吧 / 行 / 可以 / 确定 / 确认 / 没问题 / 删吧 / 删除吧 /
加吧 / 没错, optionally after 嗯 / 哦 / 啊 / 呃 or said twice, e.g. "嗯，好的",
"好，删吧" or "对对对"). A negation (不好, 不对, 别, 取消, 算了, 好，等一下) is
a no. Anything else, including sentences that merely contain 是 or 好
(还是明天吧, 是谁), a lone 嗯 and questions such as 可以吗？, is asked once
more ("不好意思，我没听清。") and then cancelled; it is never a yes. History screens stay in English.

Her app guides her itself: the sign-in screen asks for "the sign-in code you
were sent", and after signing in a card lists only the steps still left for
her (add the app to the home screen; connect Google, including what to tap
on Google's "unverified app" screen). Each step disappears once done, and
"知道了" (Got it) hides the card until she next opens the app. Steps before
she can reach the app (installing Tailscale, accepting your share, opening
the link) are yours to send her.

One-time setup on the PC (PowerShell, in the project folder):

1. **A Google OAuth client for the web** (your `credentials.json` is a
   Desktop client, which only allows localhost): console.cloud.google.com ->
   your project -> APIs & Services -> Credentials -> Create credentials ->
   OAuth client ID -> **Web application**. Authorized redirect URI:
   `https://<pc-name>.<tailnet>.ts.net:8443/google/callback`. Download the
   JSON as `data/mom/google_client.json`. On the OAuth consent screen ->
   Audience, add her Gmail address as a test user.
2. **Secrets**: create `.env.mom` with `ANTHROPIC_API_KEY=...` (your key is
   fine; `.env.mom` is gitignored). Her `WEB_TOKEN` is added to it on the
   first start.
3. **Voice**: `python -m kwil --download-voice zh_CN-huayan-medium`
   (Piper, into `voices/`). Without it the phone's own Chinese voice reads
   the replies. The Whisper `small` model downloads on the first request.
4. **Address**: create `config.mom.local.toml` (gitignored):
   ```toml
   [webapp]
   public_origin = "https://<pc-name>.<tailnet>.ts.net:8443"
   ```
5. **Run it from a clean shell** (a new PowerShell window that has not
   loaded your own `.env` or set any of your variables) and publish it on
   your tailnet. It refuses to start, naming the variables (never their
   values), if `GOOGLE_TOKEN_JSON`, `GOOGLE_CLIENT_JSON`, `GOOGLE_TOKEN`,
   `GOOGLE_CREDENTIALS`, `HISTORY_PATH`, `WEB_TOKEN`, `NOTION_TOKEN`,
   `NOTION_ASSIGNMENTS_DB`, `PINECONE_API_KEY`, `PINECONE_INDEX`, `HA_URL`
   or `HA_TOKEN` is set in the shell but not by `.env.mom`: `.env.mom`
   cannot override them, so her instance would use your Google account,
   history, token or smart home. The refusal is printed and also written
   to her log (`logs/mom.log`), since the sign-in task runs hidden. The
   same check runs whenever `[webapp] google_connect` is on. Your team chat
   tokens don't matter here: an instance started with an `--env-file`
   other than `.env` never turns team chat on. School deadlines are off in
   such a copy too unless its config gives it its own `[student] dir`
   (otherwise it would share your deadlines file); `config.mom.toml` sets
   `data/mom/student` and leaves them off. Notifications are off there too
   unless it sets its own `[push] owner_dir` (otherwise its subscriptions
   would replace yours); `config.mom.toml` sets `data/mom/student`.
   ```powershell
   python -m kwil --mode web --config config.mom.toml --env-file .env.mom --show-token
   tailscale serve --bg --https=8443 http://127.0.0.1:8766
   ```
   Share the PC with her (Tailscale admin console -> Machines -> the PC ->
   Share), so her phone's Tailscale can open that address and nothing else.
6. **On her phone**: open the address, enter the token once, Share -> Add to
   Home Screen. Then ⚙ -> **连接 Google 账户**, sign in, allow Calendar and
   Gmail. Google shows "Google hasn't verified this app" while the consent
   screen is in Testing: tap Advanced -> continue. She lands back in the app
   and can ask "我明天有什么安排？".

While the consent screen is in Testing, Google expires the sign-in after 7
days; she taps the button again (it then reads 重新连接 Google 账户). To
disconnect her account, delete `data/mom/google_token.json`, restart, and
remove the app at myaccount.google.com -> Security -> Third-party access.
`[webapp] google_connect` and `public_origin` are off and empty in your own
`config.toml`, so your instance is unchanged.


**Start them automatically.** Each family copy (any `config.<name>.toml`
with its `.env.<name>`, e.g. Mom's and Kai's) can start by itself when you
sign in to Windows and restart after a crash:

```powershell
.\scripts\install_family_tasks.ps1            # once: installs \Kwil\Family-<name> tasks
.\scripts\install_family_tasks.ps1 -Restart   # after updating the code
.\scripts\install_family_tasks.ps1 -Uninstall
```

A copy already running in a window is stopped first (you can then close the
window). Installing the tasks leaves your own app alone (that's
`install_windows_service.ps1`'s job), but `-Restart` covers **every** app
here, yours included (it runs `python -m kwil.deploy --no-pull
--restart`: the go-live check, then each app restarts once it is idle; one
still in the middle of a turn after 2 minutes is skipped and named; your
own app under its host supervisor is restarted by the supervisor).

## Invite family (cloud)

On the Render server you can give someone their own account on your
assistant, with their own conversation history and language (English or
Chinese), without sharing `WEB_TOKEN`. The server profile turns this on
(`[family] enabled`); on a PC it is off unless you set it.

Invites are managed from the cloud server's own address: open
`https://<cloud>/` (e.g. `https://hey-claude.onrender.com/`) directly. With
failover the app talks to whichever backend is active, normally your Pi or
PC, where `[family]` is off, so the **Invite someone** menu only shows
while the cloud is the active backend.

1. Signed in with your token at the cloud's address, open ⚙ -> **Invite
   someone**, enter their
   name and language (and their time zone, e.g. `Asia/Shanghai`), pick the
   **Account** type (below; **Beta** is preselected), then
   **Create invite link**. The phone's share sheet opens (or the link is
   copied). Send it to them however you like. The link points at the
   server itself (`[webapp] public_origin`, else with failover the cloud's
   address, `APP_ORIGIN`).
2. They open the link on their phone: it signs that phone in as them and
   reloads the page in their language. Share -> Add to Home Screen.
3. The link works once and for `invite_days` (30 days). If it expired, or
   they get a new phone, tap **New link** next to their name (the old link
   stops working).
4. **Remove** (also at the cloud's address) revokes their Google and
   Notion sign-ins at Google and Notion (best effort: a failure never
   stops the removal), signs them out everywhere and deletes their
   conversations and tokens (if a file can't be deleted right away, it is
   retried later).
5. They can also manage their own account from ⚙ on their phone:
   **Download my data** saves a zip of their conversations (JSON, one file
   each) and profile (name, language, time zone, tier, created date),
   never tokens or keys; at most `[family] export_max_mb` (50 MB), one at a
   time, three an hour. **Delete my account** (type `DELETE` or their
   name, then confirm; it can't be undone) revokes their Notion and Google
   sign-ins at Notion and Google, signs them out everywhere and deletes their whole folder,
   exactly like **Remove**, and removes any download of theirs still
   being made (zips are built in `<family dir>/.exports/` and deleted when
   the download ends). You can't delete or export your own (owner)
   account this way. Render's disk snapshots keep a copy until they
   expire.

**Account types (tiers).** Each person is either **Family** or **Beta**
(`tier` in `users.json`; the owner's list marks beta people "· Beta"):

- **Family**: everything below, including their own Gmail (read, drafts).
  People invited before tiers existed are Family automatically.
- **Beta** (the default for a new invite): an invite-only public beta
  tester. Same chat, voice, web search and history, but Google is
  **Calendar only**: no Gmail tools, and their Google consent screen asks
  only for `calendar.events` plus the optional read-only calendar list and
  settings scopes, even with `[gmail] enabled`. Why: Gmail scopes are
  "restricted" for Google, so a public app that asks for them needs a paid
  third-party security assessment every year; Calendar scopes don't.
  Beta testers sign in through a **separate OAuth client** in its own Google
  Cloud project (`GOOGLE_PUBLIC_CLIENT_JSON` or `[family]
  public_google_client`), whose consent screen lists nothing but Calendar.
  Until it is set, beta testers can't connect Google (the app says "Google
  isn't set up for beta accounts yet"); they never fall back to the family
  client, whose consent screen lists Gmail.

**Their own Anthropic key (bring your own key).** Beta testers pay for their
own Claude use: until a tester adds their own key, every turn answers "Add
your Anthropic API key in Settings to start" and nothing is sent to
Anthropic on your key. Family members may add one too (then their use is on
their bill); without one they use yours, as before. What to tell a tester:

1. Make an account at **console.anthropic.com**, add a payment method or
   credits under Billing (you can set a monthly spend limit there), then
   **API Keys -> Create Key** and copy it (it starts with `sk-ant-`).
2. In the app: ⚙ -> **Your Anthropic API key**, paste it, **Save key**. The
   app checks it with Anthropic (one models-list call, no tokens) before saving;
   afterwards Settings shows only its last four characters and an estimate
   of this month's cost on it. **Replace key** / **Remove key** are there too.

The cost is theirs: chats, voice and their history titles all go on their
key, and none of it counts toward your `month_usd`. A key Anthropic later
rejects says "Your Anthropic API key was rejected. Check it in Settings."
Keys are stored encrypted (`<dir>/<id>/anthropic_key.enc`, SECURITY.md)
with a server secret, `USER_DATA_KEY`, which `render.yaml` has Render
generate. Keep that value: if it changes, every tester has to enter their key
again (nothing else breaks). Without it the server stores no keys and says
why.

What they get: chat and voice with Claude, web search, their own
history (in `<dir>/<id>/history.sqlite`, `/data/users` on Render, never
synced to your Pi or PC; each person's is capped at `[family]
max_history_mb`, 20 MB, oldest unpinned conversations first), and
everything in their language: replies, the
assistant's own sentences ("Please say yes or no", "I didn't catch that")
and, for Chinese, speech recognition with the multilingual Whisper model
(`zh_stt_model`, `small`) and the Mandarin Piper voice (`zh_piper_voice`).
Those load the first time a Chinese-speaking person talks and are shared by
everyone who speaks Chinese; English speakers keep the server's own.

**Their limits.** Everyone you invite (family and beta alike; never you)
can send at most `turns_per_minute` messages a minute (6) and
`turns_per_day` a day (200), typed, push-to-talk or hands-free together,
and have `stt_seconds_per_day` seconds of speech turned into text a day
(1800, half an hour). Past one, the app says so in their language (e.g.
"You've reached today's limit of 200 messages. You can talk to me again
tomorrow.") instead of answering: nothing goes to Claude and their
conversation is left as it was. When their speech time is used up they can
still type. A day is their own calendar day (the time zone you gave them,
else UTC). The counts are kept in memory, so restarting or redeploying the
server resets them. `0` turns a limit off.

**Their own Google.** Once it is set up (below), they tap ⚙ -> **Connect
Google account** on their own phone and sign in to *their* Google account.
Then they can ask about their calendar (add, move, delete; deletes ask
first) and their Gmail (read, and draft replies, never send), exactly like
yours. Their token is kept in their own folder
(`<dir>/<id>/google_token.json`), is never mixed with yours (your Google on
the server is the one you connected in the app, `/data/google_token.json`,
else `GOOGLE_TOKEN_JSON`; "Run it in the cloud" step 3), and **Disconnect Google account**
or removing them asks Google to end access (best effort: a failure there
never stops the delete) and deletes it. One-time setup for you:

1. Google Cloud console, the same project as your own sign-in -> APIs &
   Services -> Credentials -> **Create credentials -> OAuth client ID ->
   Web application**. Authorized redirect URI:
   `https://<cloud>/google/callback` (e.g.
   `https://heykwil.com/google/callback`). Download its JSON. Your own
   **Connect Google account** on the server signs in with this client too.
2. In Render -> hey-claude -> Environment, set `PUBLIC_ORIGIN` to
   `https://<cloud>` and paste that JSON's whole contents into
   `GOOGLE_WEB_CLIENT_JSON`, then Save.
3. The app must be **published** (step 3 of "Run it in the cloud" below):
   in Testing mode only listed test users can sign in, and only for 7 days.
   They see Google's "unverified app" warning once; their guide card tells
   them to tap Advanced -> Go to ... (unsafe).

Without step 2 everything else works and their Connect button stays hidden.

For **beta testers**, repeat steps 1-3 in a *new* Google Cloud project
(e.g. "Hey Claude Beta") whose OAuth consent screen lists only the
`calendar.events`, `calendar.calendarlist.readonly` and
`calendar.calendars.readonly` scopes, and paste that client's JSON into
`GOOGLE_PUBLIC_CLIENT_JSON` instead. Submit that project for Google's
verification (sensitive scopes: a privacy policy and a demo video, no paid
assessment); until then it works as an unverified app for up to 100 users.

### Notion for beta testers

Anyone you invited (Beta or Family) can connect *their own* Notion from ⚙
-> **Connect Notion**. Notion's consent page lets them pick the pages to
share; they share their Assignments database (properties `Assignment`
(title), `Status` (status with `Not started`, `In progress`, `Done`) and
`Due` (date), like yours). Then they get exactly two tools on their own
token: "what assignments do I have this week?" (`list_assignments`) and "mark
my essay done" (`update_assignment_status`, which asks first once the
conversation holds Notion text). Nothing else of Notion: no lecture recorder,
no new assignments, and never your `NOTION_TOKEN`.

- If they shared no Assignments database, or more than one, Settings says so
  ("share only your Assignments database") and the tools stay off; they tap
  **Reconnect Notion** and share just that one. There is no picker.
  Reconnecting to another workspace revokes the grant it replaces; to the
  same workspace (the same Notion bot) it just stores the new token, since
  revoking the old one could end the new grant too.
- Their sign-in is stored encrypted (`<dir>/<id>/notion.enc`, with the same
  `USER_DATA_KEY` as their Anthropic key; SECURITY.md). Without
  `USER_DATA_KEY` the server refuses to connect Notion and says why.
- **Disconnect Notion**, **Delete my account** and **Remove** revoke the
  token at Notion (best effort: a failure there never stops the delete) and
  delete the file.

One-time setup for you:

1. notion.so/profile/integrations -> **New integration**, type **Public**,
   capabilities *Read content* and *Update content* (no user information
   with email). Fill in the company name, website, privacy policy URL
   (`https://<cloud>/privacy`), terms URL (`https://<cloud>/terms`) and
   support email. Redirect URI: `https://<cloud>/notion/callback` (e.g.
   `https://hey-claude.onrender.com/notion/callback`). It doesn't need a
   Marketplace listing.
2. Copy the **OAuth client ID** and **OAuth client secret** (the secret is
   shown once) into Render -> hey-claude -> Environment as
   `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET`, with
   `PUBLIC_ORIGIN` set (the redirect URI comes from it), then Save.
3. Optional: publish an "Assignments" template page and set it as the
   integration's template; testers who pick it get a copy, which is chosen
   even if they also share another matching database.

Without step 2 (or with `[family] notion = false`) the button stays hidden.
The same integration also lets you connect your own Notion on the cloud
(⚙ -> Connections -> Notion; "Connect your services in the app (cloud)").
A custom domain later changes the redirect URI: update it in the
integration too.

What they never get: your Notion, Pinecone memory and notes, your Google
Calendar and Gmail, the dev team, the lecture recorder and tutor, your
history, failover, or the invite menu. Their **Sign out everywhere** signs
out only their own phones; yours signs out only yours. Settings: `[family]`
in `config.toml` (`enabled`, `dir`, `max_users`, `invite_days`, `google`,
`google_client`, `public_google_client`, `notion`, `max_history_mb`,
`turns_per_minute`, `turns_per_day`, `stt_seconds_per_day`,
`export_max_mb`, `zh_stt_model`, `zh_piper_voice`).

### Move a PC family copy to the cloud

Someone who has their own copy on your PC ("A Chinese instance for
someone else" above, e.g. Mom's `config.mom.toml` with `data/mom/history.sqlite`) can
move to the cloud as a family member and keep their conversations:

1. **Invite them** at the cloud's address (step 1 above) and pick
   **Family** as the account type (**Beta** is preselected). Get their
   language and time zone right: neither can be changed later. They open
   the link on their phone (step 2).
2. **Google**: on the PC, delete their old sign-in
   (`data/mom/google_token.json`) and remove the app at
   myaccount.google.com -> Security -> Third-party access, so the PC copy
   can't keep reading their Calendar and Gmail. Then they tap ⚙ -> **Connect
   Google account** in the cloud app.
3. **Copy their history** from the PC (PowerShell, in the project folder;
   the cloud's `WEB_TOKEN` goes in an environment variable, never on the
   command line):
   ```powershell
   $env:KWIL_CLOUD_TOKEN = Read-Host "Cloud WEB_TOKEN"
   python -m kwil.family_import --from data/mom/history.sqlite --to https://hey-claude.onrender.com --user Mom --dry-run
   python -m kwil.family_import --from data/mom/history.sqlite --to https://hey-claude.onrender.com --user Mom
   Remove-Item Env:KWIL_CLOUD_TOKEN
   ```
   `--user` is the name in your invite list (or their `u_...` id). It
   reads a copy of the database, so their PC copy may keep running, and
   prints only counts. Running it again skips what is already there. If
   the server answers 413, their history is bigger than `[family]
   max_history_mb` (20 MB) allows: raise it on the server and run it
   again (nothing of the refused batch was kept).
4. **Check** that their History screen in the cloud app lists their old
   conversations.
5. **Retire the PC copy**: `.\scripts\install_family_tasks.ps1 -Uninstall`
   (this removes every family task; reinstall it if another copy stays),
   move `.env.mom` (and `.env.kai`) out of the project folder, and stop
   publishing their ports (`tailscale serve --https=8443 off`, and the same
   for each copy's port).

On the cloud, finding an old conversation and continuing it by voice
("what did we say about...") is owner-only; a family member searches,
reads and continues theirs in the History screen.

## Public beta: landing page and waitlist (cloud)

The server can also serve a small public site, from the app itself (no
JavaScript, no outside fonts or scripts, the same strict CSP as the app).
It is **off** (`[webapp] public_site = false`, on the server too): the pages
promise beta tiers (Calendar only), your own Anthropic key stored encrypted,
and Notion sign-in. Those are all built; turn it on once the legal pages
are approved, and before setting up the beta Google project and the Notion
integration (their forms need the live /privacy and /terms), then set their
Render variables before inviting anyone (BLOCKERS.md, "Public beta launch";
the comment at the top of `kwil/web/landing.html` and `privacy.html`
lists the deploy checks). Then turn it on for the server with a
one-line change, through a PR like any other: `"public_site": True` in the
server profile's `"webapp"` settings (`SERVER_PROFILE` in
`kwil/config.py`). On a PC, the same in `config.local.toml`:

```toml
[webapp]
public_site = true
```

With it on:

- `https://<cloud>/welcome`: what Kwil does, "invite-only beta",
  "bring your own Anthropic API key", and the waitlist form (email and an
  optional "what would you use it for", at most 300 characters).
- `https://<cloud>/privacy` and `https://<cloud>/terms`: the beta's privacy
  notice and plain-language terms.

`/` stays the app: signed out, it is still the sign-in page, with a
**What is Kwil?** link to `/welcome` (only while the site is on). Invited people and your own
phone keep the address they already have, and the PWA's home-screen icon
keeps opening the app, not a marketing page. Share `/welcome` publicly.

Settings (`[webapp]` in `config.toml`):

- `support_email`: the contact shown on the privacy and terms pages
  (empty = "the person who runs this server").
- `legal_draft = true` shows a "Draft: Kyle to review" note on both pages.
  Read them, change the wording in `kwil/web/privacy.html` and
  `terms.html` if needed, then set it to `false`.
- `waitlist_file`: where entries are kept (`/data/waitlist.json` on Render,
  on the disk; empty = waitlist closed, the default on a PC).

**Inviting someone from the waitlist.**

1. Signed in with your token at the cloud's address, open
   `https://<cloud>/waitlist`. It lists each address, its note and date
   (only you can see it; a family member gets "not found").
2. In the app, ⚙ -> **Invite someone** (see "Invite family (cloud)" above)
   makes their link. Email it to them yourself; nothing is sent
   automatically.
3. Back on `/waitlist`, **Remove** their entry. Remove is also how you
   delete someone who asks.

Or with curl (the entry ids come from the list; an address never goes in
a URL, so it never lands in an access log):

```
curl -H "Authorization: Bearer $WEB_TOKEN" https://<cloud>/api/waitlist
curl -X DELETE -H "Authorization: Bearer $WEB_TOKEN" -H "Content-Length: 0" \
     https://<cloud>/api/waitlist/<id>
```

The waitlist keeps at most 2000 entries; addresses are lower-cased and
listed once (a repeat gets "thanks", and once the list is full everyone
gets "full", so the form never tells whether someone else is on it). Each network address can post 5 times an hour, and
a hidden field that only bots fill in drops their posts without a word. The
log records only counts, never an address or a note.

## Run it in the cloud (Render)

The phone web app can also run 24/7 on a small paid server at
[Render](https://render.com), so it works from your iPhone with the PC off.
The repo has everything: a `Dockerfile` (Whisper and the Piper voice are
baked into the image, so a restart downloads nothing) and a `render.yaml`
Blueprint with a `hey-claude` service (it keeps that name after the rename
to Kwil: a renamed Blueprint service would be a new one with an empty disk;
change only its display name in Render's dashboard). In the cloud it runs with
`ASSISTANT_PROFILE=server`: no mic, speakers or wake word, Piper voice (or
espeak-ng if the voice is missing), no "open in browser" tool, and logs go
to Render's log viewer. Secrets live in Render's settings, never in the
image; the only ones on its disk are the Google sign-ins made in the app
(yours, if you connect Google there, and invited people's) and invited
people's encrypted keys. It is on the public internet, so it is hardened
(SECURITY.md, "Cloud server"): only the login page, `/healthz` and the app
icon work without the token, and wrong tokens slow the sender down and lock
it out (the right token always works).

Cost: about $25.25 a month: the Standard plan (2 GB RAM, $25) plus a 1 GB
persistent disk ($0.25/GB). Speech recognition with
Whisper `base.en` plus the Piper voice was measured at up to 564 MB, which
doesn't fit the $7 Starter plan's 512 MB (PROGRESS.md, "Cloud hosting").
For Chinese-speaking family members the image also holds Whisper `small`
(about 480 MB) and the Mandarin Piper voice (about 60 MB); they load into
memory only once one of them talks (estimated at 0.5-0.7 GB more, not yet
measured on Render), which the Standard plan has room for. Build with
`--build-arg FAMILY_ZH=0` for a smaller image without them.
Render's free Hobby workspace is enough; you pay only for the service. To
save money you can switch the service to Starter and set `WHISPER_MODEL` to
`tiny.en` (peaks around 470 MB: it fits, with little room to spare, and
mishears more often).

**Persistent disk.** The service has a 1 GB disk mounted at `/data`, and
the image sets `DATA_DIR=/data` and `HISTORY_PATH=/data/history.sqlite` for
the conversation-history and lecture features. Everything outside
`/data` is wiped on each deploy. Watch the disk graph in Render (hey-claude
-> Disks, or Metrics): the one 1 GB disk is shared by your history, lecture
audio, each invited person's history (up to 10 people x 20 MB,
`[family] max_history_mb`) and "Download my data" exports (up to 50 MB
each while they are being made, `[family] export_max_mb`); grow it in
Render before it fills. Render's free instance type can't have a disk, which is one more
reason the service is on a paid plan. Render takes daily snapshots of the
disk; for a copy somewhere else, download a backup now and then
(`/api/backup`, below). With a disk, Render runs exactly
one instance and a deploy has a short gap of downtime.

1. **Push** this branch to GitHub (merged into master).
2. **Create the service.** On render.com: sign up (GitHub login), add a
   payment card, then **New -> Blueprint**, pick this repository and the
   `master` branch. Render reads `render.yaml` and shows two services,
   `hey-claude` and `pinecone-mcp`. It asks for each secret marked
   `sync: false`; paste them in yourself (they are never in the repo):
   - `ANTHROPIC_API_KEY`, `PINECONE_API_KEY`: the same values as in your
     `.env`. `NOTION_TOKEN` is optional: leave it empty to connect Notion
     from the app instead ("Connect your services in the app", below).
   - `GOOGLE_TOKEN_JSON` (optional now): open `token.json` (in the project
     folder on the PC) in Notepad, select all, copy, paste. The whole file,
     braces included. You can leave it empty and connect Google in the app
     instead (step 3); once you do, that sign-in wins, and this stays the
     fallback.
   - `GOOGLE_CLIENT_JSON`: the same for `credentials.json` (only needed
     with `GOOGLE_TOKEN_JSON`).
   - `ASSISTANT_TIMEZONE`: your time zone, e.g. `America/New_York`, so
     the time Kwil tells you and "tomorrow" are yours (the server runs on
     UTC; without it Kwil is hours off, and the log warns at startup).
   - `WEB_TOKEN` is generated by Render; leave it.
   - `USER_DATA_KEY` is generated by Render too (it encrypts beta testers'
     own Anthropic keys and Notion tokens, and your own Connected services);
     leave it, and never change it:
     a new one makes every stored key and token unreadable.
   - `PUBLIC_ORIGIN`: the exact https origin people reach the server at
     (e.g. `https://hey-claude.onrender.com`, no trailing slash). The
     Google and Notion redirect URIs and the cookies come from it, so pick
     a custom domain before inviting anyone.
   - `GOOGLE_WEB_CLIENT_JSON`: the Web application client (README "Invite
     family", "Their own Google") that your own **Connect Google account**
     and family members' sign-ins use.
   - `GOOGLE_PUBLIC_CLIENT_JSON`: beta testers' Calendar-only Google
     client, in its own Google Cloud project (README "Invite family");
     empty = beta testers can't connect Google.
   - `NOTION_OAUTH_CLIENT_ID`, `NOTION_OAUTH_CLIENT_SECRET`: the public
     Notion integration for invited people's own Notion (README "Invite
     family"); empty = the Connect Notion button stays hidden.
   - `SENTRY_DSN`: optional error reports ("Uptime, error tracking and
     backups", below); empty = off.
   Click **Apply**. The first build takes about 10 minutes.
3. **Publish the Google app.** Google Cloud console -> APIs & Services ->
   OAuth consent screen (Google Auth Platform -> Audience) -> **Publish
   app**. In "Testing" mode Google expires refresh tokens after 7 days, which
   would break the calendar every week. Google then shows an "unverified
   app" warning when you sign in: that is expected for a personal app (click
   Advanced -> Go to Kwil). After publishing, open the app signed in with
   `WEB_TOKEN`, tap ⚙ -> **Connect Google account** and sign in: the
   server keeps that (long-lived) token on its disk at
   `/data/google_token.json` (`[webapp] owner_google`, on in the server
   profile) and uses it for Calendar, Gmail and the student sync instead of
   `GOOGLE_TOKEN_JSON`. **Disconnect Google account** there asks Google to
   end that access, deletes the file and goes back to `GOOGLE_TOKEN_JSON`
   (if set). Google may end your PC copy's sign-in at the same time (same
   Google project); if Calendar stops on the PC, run
   `python -m kwil.tools.google_auth` there again. The `GOOGLE_TOKEN_JSON`
   fallback is that same PC token, so if Google ended it, it stops working
   on the server too: after re-running the sign-in on the PC, paste the new
   `token.json` into `GOOGLE_TOKEN_JSON` in Render again. The old way still works:
   sign in once more on the PC, delete `token.json`, run
   `python -m kwil.tools.calendar`, and paste the new `token.json` into
   `GOOGLE_TOKEN_JSON` in Render (Environment -> Save; it restarts).
   **If Google ends the sign-in** (revoked, or 7 days in Testing mode),
   the server notices at the next Calendar or Gmail call ("could not be
   refreshed (invalid_grant)" in the log) and ⚙ then says **Google sign-in
   expired. Reconnect to keep using it.** instead of "connected", with the
   **Reconnect Google account** button: the same sign-in as above, whose
   token then wins over `GOOGLE_TOKEN_JSON`. Family members see the same
   for their own account. The flag lives in memory, so after a restart it
   shows again at the first refused call; `python -m kwil --check` asks
   Google directly ("Google sign-in" line).
4. **Check it.** In Render -> hey-claude -> Shell, run
   `python -m kwil --check` (no mic or speaker checks on a server).
   The Logs tab should show "Speech models loaded".
5. **On the iPhone.** Render -> hey-claude -> Environment -> `WEB_TOKEN` ->
   reveal and copy it (e.g. AirDrop or a note to yourself). Open
   `https://hey-claude.onrender.com` (the address at the top of the service
   page; Render may add a suffix) in **Safari**, paste the token once,
   allow the microphone, then Share -> **Add to Home Screen**. Open it from
   the home screen from now on; it stays signed in (90 days, renewed
   each time you use it).

If Google stops working (Render logs say "Google Calendar sign-in could not
be refreshed"), sign in again on the PC as in step 3 and paste the new
`token.json`. To sign every phone out, change `WEB_TOKEN` in Render (any 32+
random characters) and save. `WHISPER_MODEL` picks the speech model:
`base.en` (default, more accurate) or `tiny.en` (smaller, faster, more
mistakes); not the PC's `small.en`, whose memory use on Render isn't
measured yet. Beam search 5 and the vocabulary prompt apply here too. For
better accuracy on the cloud, set `STT_ENGINE` to `openai` or `deepgram`
and add that service's key (`OPENAI_API_KEY` or `DEEPGRAM_API_KEY`): see
[Cloud speech-to-text](#cloud-speech-to-text-optional-paid). Run the same image anywhere with Docker:
`docker build -t kwil .` then `docker run -p 10000:10000 -e WEB_TOKEN=...
-e ANTHROPIC_API_KEY=... kwil` (it expects a TLS proxy in front, and
trusts forwarded headers only from 10.x addresses, Render's; set
`CLOUD_TRUSTED_PROXIES` to your proxy's network, e.g. `172.18.0.0/16`, or to
nothing). After the first Render deploy, the log's first five "Proxy check"
lines show what Render's proxy sends; see BLOCKERS.md.

### Uptime, error tracking and backups

**Uptime alerts (free).** At [UptimeRobot](https://uptimerobot.com) (free
plan): sign up, **Add New Monitor** -> HTTP(s), URL
`https://hey-claude.onrender.com/healthz` (your service's address), every
5 minutes, and your email as the alert contact. `/healthz` needs no token
and says nothing but "ok" and the version, so the monitor holds no secret.
If the PC watchdog runs (README "Health checks and safe updates"), it can
check the cloud copy too: set `[watch] cloud_url =
"https://hey-claude.onrender.com"` in the PC's `config.toml`. It then
checks that address's `/healthz` every 5 minutes and alerts (the phone
banner, the dev team coordinator) when it starts failing and when it
recovers; it never restarts or rolls back the cloud (Render does its own
health checks on `/healthz` and restarts the service).

**Error tracking (optional, free tier).** Crash reports go to
[Sentry](https://sentry.io) only when `SENTRY_DSN` is set. `sentry-sdk`
is in `requirements.txt` (and so in the image), but without a DSN it never
starts: nothing is sent anywhere and the app runs exactly as before. On
Render: create a Python project in Sentry and copy its DSN into
`SENTRY_DSN` (Environment -> Save; it restarts). On a PC or Pi: put
`SENTRY_DSN=...` in `.env` (a family copy's `.env.<name>` must set its own;
one inherited from the shell stops it, as with the other secrets). An
install from before this change gets `sentry-sdk` with the next
`pip install -r requirements.txt`; until then a DSN only logs one warning.
A report holds the error's type, where in the code it happened (file,
function, line) and the request's method and path; never the error's
message, what anyone said or typed, replies, request bodies, headers,
cookies, query strings, IP addresses, the machine's name or local
variables, and keys and tokens are masked. No performance tracing, and no
Sentry headers are added to the app's calls to Claude, Google or Notion
(SECURITY.md, "Error tracking and backups").

**Backups.** Render snapshots the `/data` disk once a day (how long it
keeps them: Render's disk docs); a snapshot restores the whole disk in
place (Render -> hey-claude -> Disks), so it helps when a deploy or a bug
damages the data, but it lives in the same Render account and can't hand
you one file. For a copy you keep yourself,
sign in on the phone or a computer as the owner and open
`https://hey-claude.onrender.com/api/backup`: it downloads a zip with
- `history.sqlite`: your conversation history (a consistent copy, taken
  while the app keeps running),
- `users/users.json` and `users/<id>/history.sqlite`: the family list and
  each member's own history,
- `memory.sqlite` and `users/<id>/memory.sqlite`: everyone's memory and
  projects (instructions and memories; the project files themselves are
  left out, see Projects),
- `waitlist.json`, if there is one, and `MANIFEST.json` (when, which
  version, each file's size, how many project files were left out).

It never holds Google tokens, keys or any `.enc` file, signed-in sessions,
`.env`, logs or lecture audio, or anyone already removed, so the zip is
safe to store off-site, but it is everyone's conversations: keep it
private. A family member's sign-in gets "not found". Above
`[webapp] backup_max_mb` (200) it is refused; raise the setting if the
history grows past it. To restore one database, stop the service and copy
the file back to `/data` (Render Shell), or open it with any SQLite tool.

## Connect your services in the app (cloud)

On the cloud app you can sign in to your own services from the phone
instead of pasting their secrets into Render's environment: ⚙ ->
**Connections**. That list has one row per service (Google, Notion,
Canvas, Home Assistant, Spotify; an invited person also sees their own
Anthropic key there) with the service's icon, connected ones first. Each
row shows a status ("Connected", "Sign-in expired") or **Connect**; tap it
to open that service's sheet with its explanation, buttons and
**Disconnect**. A service this server can't offer isn't listed. Notion's and Home Assistant's rows say how they are set
up now:

- **Set on the server.** Its token is in the environment (`NOTION_TOKEN`,
  `HA_TOKEN`). That always wins, exactly as before, and there is nothing
  to tap. To switch to signing in from the app, delete that variable in
  Render (Environment -> Save), then come back here.
- **Not connected.** Open the row, tap **Connect** and sign in on the
  service's own page (in the same tab; you come back to the app). Its
  tools appear at once, no restart.
- **Connected.** Open the row and tap **Disconnect** (it asks first): Kwil
  revokes the sign-in at the service, deletes it from the server's disk,
  and the tools go away at once.
- **Sign-in expired.** The service stopped accepting the sign-in; open
  the row and connect again.

Only you (the `WEB_TOKEN` owner) see this; invited people get "not found".
Sign-ins are stored encrypted with `USER_DATA_KEY` in `[connect] dir`
(Render: `/data/owner`, on the disk); without `USER_DATA_KEY` the server
refuses to connect anything and says why. Tokens and codes are never
logged. Settings: `[connect]` in `config.toml` (`enabled`, `dir`,
`notion`, `home_assistant`). SECURITY.md, "Owner's connected services",
has the details.

**Notion.** Uses the same public integration as invited people's Notion
(`NOTION_OAUTH_CLIENT_ID` / `_SECRET`, redirect URI
`<PUBLIC_ORIGIN>/notion/callback`: nothing new to register). On Notion's
consent page, tick every database Kwil uses for you: your **Assignments**
database (the one in `[notion] database_id`), and the pages that hold or
will hold **Lecture Notes**, **Chat Digests** and the **Hey Claude
coordinator inbox** (or the page they sit in, so Kwil can create them
there). Your Assignments tools, lecture notes, team chat digests and the dev
team inbox then all use this sign-in; Notion's access token is refreshed
automatically when it ends. The integration needs the **Insert content**
capability for this (lecture notes, digests and the inbox create pages);
invited people's two tools still never create anything.

**Home Assistant.** Uses Home Assistant's own sign-in (nothing to
register: Kwil's address is its client id). Type your Home Assistant's
**public https address** (e.g. `https://xxxx.ui.nabu.casa` from Nabu Casa,
or a Cloudflare tunnel's address) and tap **Connect**; sign in with the
dedicated non-admin user from "Smart home" below. The server must be able
to reach it over the internet, so a `homeassistant.local` or `192.168.x.x`
address is refused (as are plain `http://` addresses, other ports than
443, and anything that resolves to a private or cloud-metadata address).
Kwil keeps the address and the refresh token (encrypted); access tokens
last 30 minutes and are renewed in memory.

## Email, notes, lectures and homework help

### Google Calendar by voice
Beyond "what's on my calendar", the calendar tools cover most of what the
Google Calendar app does:

- **Colors and labels.** "Make it red", "Color the lab meeting Basil",
  "Label it School". Google's 11 event colors work by name (Lavender, Sage,
  Grape, Flamingo, Banana, Tangerine, Peacock, Graphite, Blueberry, Basil,
  Tomato) or plain words like red or light blue. The named **event labels**
  you made in Google Calendar work by their names too. Labels are read
  from Google; create and rename them in the Google Calendar app.
  `[calendar] color_labels` in `config.toml` adds your own words, e.g.
  `color_labels = { Homework = "School", Gym = "Tangerine" }`.
- **Your own color habits.** When Kwil builds your schedule it first looks
  at how you colored your own events over the last two weeks
  (`calendar_color_habits`: per course code or title, the color or label
  you used most) and colors the new events the same way. It only uses
  colors and labels that already exist: an unknown label name is refused,
  never created, and if nothing similar is found it leaves the default
  color and says so. Label names need the extra calendar permissions from
  the newer Google sign-in; without them Kwil says it can't read your
  labels instead of guessing one. Invites and all-day or untitled events
  don't count.
- **Repeating events.** "Add gym every Tuesday and Thursday at 7 AM for
  ten weeks". To move, change or delete a repeating event, say whether you
  mean just this one or all of them; deleting a whole series asks "delete
  every ...?" first. Moving a weekly series to another weekday works; a
  series on several weekdays or fixed dates of the month can only change
  its time (otherwise it offers to make a new series).
- **Reminders, busy/free and privacy.** "Remind me 10 minutes before and
  at the start", "no reminders", "show me as free", "make it private".
- **Guests and Meet.** "Invite ana@uni.edu", "Add a Meet link". Google
  emails an invite, so adding or removing guests always asks first and
  names every address. Moving or deleting an event doesn't ask Google to
  email its guests; tell them yourself, or do it in Google Calendar.
- **What asks first.** Adding, moving or changing an event just happens
  when you ask (Claude is told not to ask "are you sure?" itself). Deleting,
  inviting or removing guests and answering an invite always ask. After
  something written by someone else came into the conversation (a web
  page, an email, an invite from someone else, an event Gmail made or one
  your guests may edit, a calendar someone shared with you, an attached
  file), every change asks too, until the conversation ends. Your own
  events and their notes, holidays and birthdays don't count, so
  "what's on tomorrow?" then "move the dentist to ten" just moves it.
- **Invites.** "Accept the project sync invite", "Tell them maybe". This
  always asks first, and reads out any note it would send.
- **Free time and search.** "When am I free for an hour tomorrow
  afternoon?", "When is my next dentist appointment?"
- **Other calendars.** "What's on my Classes calendar?", "What's on all my
  calendars this week?" (up to 10 calendars), "Which calendars do I have?"

Labels and other calendars need two read-only permissions that a
`token.json` from before this feature doesn't have. Everything else keeps
working without them; to add them, sign in once more (the startup line and
`--check` remind you):

```powershell
python -m kwil.tools.google_auth            # or add --headless without a browser
```

This needs `google-api-python-client` 2.199 or newer (event labels); the
automatic updater doesn't install new requirements, so run
`pip install -r requirements.txt` on the PC and the Pi after updating.

Not covered: focus time, out of office and working location (Google only
allows them on work or school accounts), file attachments, and Google's
"quick add" text box. Creating, renaming or deleting labels and calendars
is left to the Google Calendar app.

### Email triage by voice (Gmail)
"Anything important in my email?", "Read the one from Professor Smith",
"Reply that I'll add error bars." The assistant lists unread or important
mail from the last day (sender, subject, a one-line gist, at most
`[gmail] max_summary` messages), reads one message in plain text, and saves
a reply as a **Gmail draft** after you say yes. You review and send drafts
in Gmail yourself. The sign-in asks for `gmail.readonly` and
`gmail.compose`. Google has no drafts-only permission, and `gmail.compose`
would also allow sending, so the code blocks it instead: there is no send
tool, and the Gmail connection refuses any send (or delete, or settings)
call. Gmail access is requested only while `[gmail] enabled = true`; with
it off, sign-in asks for Calendar alone.

Gmail uses the same Google OAuth client as Calendar, but a `token.json`
from before this feature only has the Calendar permission. Sign in once
more so Google can ask for the new ones (Calendar keeps working until you
do; the startup line and `--check` remind you):

```powershell
python -m kwil.tools.google_auth            # or add --headless without a browser
```

In the Google Cloud console the Gmail API must be enabled for the
"Hey Claude" project (APIs & Services -> Library -> Gmail API -> Enable).
For a cloud host, tap ⚙ -> **Connect Google account** in the app (it asks
for every scope at once), or put the whole contents of `token.json` in the
`GOOGLE_TOKEN_JSON` secret instead of copying the file.

Email is written by other people, so it is treated like web pages: while
an email is in the conversation, anything that changes something
(calendar, Notion, notes, drafts) asks for a spoken yes first, and email
text never goes into the log file (see SECURITY.md).

### Memory

Kwil remembers two kinds of things, both on your own machine (one SQLite
file per person, `[memory] local_path`; no Pinecone needed):

- **Your instructions**: what you type in **Settings -> Memory** ("Always
  color-code my schedule", "Call me K"), up to 4000 characters.
- **Things Kwil remembered**: when you say "remember...", "always..." or
  "from now on...", Kwil saves a short memory (up to 500 characters, 200
  memories). It doesn't save health, religion or politics unless you ask
  it to remember that exact thing. "Forget that" deletes one after a
  spoken yes; "actually, make it Tuesdays" edits one; "what do you
  remember about me?" lists them.

Both are read when a conversation starts and given to Claude for the whole
conversation (in the cached system prompt), so a change applies from your
next conversation. A memory saved while a web page, email or other outside
text was in the conversation asks you first (with the whole text) and is
marked **Outside source**: Kwil still follows it as your preference, but while
one is loaded every change asks for a yes (as after reading an email). Tap
**Keep as mine** on it in Settings to vouch for it. A memory you save in a
conversation without outside text is never marked, even when an untrusted
one is loaded.

**Settings -> Memory** (everyone, family members too, each their own):
edit your instructions and Save; edit or delete each memory (delete asks
first); **Pause memory** (nothing new is saved and no memories are used;
your instructions still apply); **Export** (a JSON file); **Forget all**
(asks first, then deletes every memory and your instructions; the file is
scrubbed, nothing is left to recover). Incognito conversations save or
change nothing (forgetting still works). Forgetting by voice also takes the
memory out of your other open conversations from their next turn. A
family member's memory is in their own folder, included in "Download my
data" and deleted with their account; the owner's backup zip has
everyone's. With failover, each backend (Pi, PC, cloud) keeps its own
memory for now; it isn't synced, and the screen says where yours is
("Saved on this server (cloud)").

Kwil can also look things up in your past conversations ("what did we
decide about my physics schedule?"; see [Conversation history](#conversation-history)).
Settings for that, in `[memory]`:

| Setting | Default | What it does |
|---|---|---|
| `[memory] embeddings` | "off" | "off" = keyword search of past chats; "fastembed" = also by meaning (optional package, vectors in your own history file) |
| `[memory] embedding_model` | "BAAI/bge-small-en-v1.5" | The fastembed model |
| `[memory] embedding_dir` | "models/fastembed" | Where `python -m kwil.recall --download` puts it |
| `[memory] embedding_min_score` | 0.5 | How close a summary must be to count as a match by meaning |

`[memory] backend = "pinecone"` adds the older Pinecone memory on top:
notes recalled before each reply, conversation summaries, quick notes
(`remember_note`) and the weekly review below. "Remember..." still saves
to the local memory above with either backend (it is what every new
conversation is told); Pinecone gets quick notes and summaries. If Pinecone answers "over quota" (429), Kwil logs
"Memory: Pinecone over quota" once and leaves Pinecone alone for 10
minutes; your local memory keeps working.

### Quick notes
With `[memory] backend = "pinecone"`: "make a note that the lab report
needs error bars" saves a short note in
Pinecone (your `voice-assistant` namespace, tagged as a note);
"What did I need for the lab report?" finds it by meaning. You can also ask
for your recent notes or to delete one (it asks first). With conversation
history on, a saved note also shows up in that conversation. Notes need
`[memory] backend = "pinecone"`; `[notes] enabled` turns them off.

### Review what it remembers
"What did you remember about me this week?" gives a short spoken summary of
this week's conversation summaries and notes ("...last week?" works too).
"Forget the one about cilantro" deletes it after a spoken yes (the same
`delete_note` as for notes, which now also takes conversation summaries).

With `[memory] backend = "pinecone"`, **Memory** in the phone app's
settings menu also lists ("Long-term notes") everything the
assistant saved in Pinecone, newest first and grouped by week, with the
date, whether it is a conversation summary or a note, and an **Outside
source** badge on ones saved while a web page or email was in the conversation.
Delete removes one; "Delete all from this week" removes that week's notes
(both ask first). Only what the assistant wrote itself (ids starting with
`voice-` or `note-` in `[memory] namespace`) is listed or deleted; your
other Pinecone notes are never touched. Old vectors without a date show
under "Date unknown". That part is the owner's only: family members have no
Pinecone memory and don't see it.

Optional weekly nudge: with `[memory] weekly_review = true`, once a week
the first conversation that has notes from this week starts with "I saved N
notes this week, want to review them?". Off by default; the time of the
last offer is kept in `[memory] review_state_file`
(`data/memory_review.json`). With failover, each backend keeps its own, so
you may hear it once per backend.

### Record a lecture (phone app)
In the phone app, open the **Lecture** tab, pick the course (the options
come from the Course property of your Notion Assignments database), and
tap Start. Ask the professor first: the screen shows a reminder. The page
keeps the screen awake, shows the elapsed time, and uploads the audio every
10 seconds in small pieces; if the network drops, it keeps recording and
resends from where the PC left off, so a 75-minute lecture survives Wi-Fi
blips (keep the app open in the foreground). Tap Stop when the lecture ends.

The PC then transcribes the recording with faster-whisper (`[lecture]
model`: "base.en", or "tiny.en" for speed; progress shows in the app),
asks Claude for notes (Summary, Key concepts, Equations in LaTeX, Action
items, Questions to ask), and creates a page in a **Lecture Notes**
database inside your Fall 2026 Assignments page, with Title, Course, Date
and Duration. The database is created automatically the first time (with
the same Course options); if the integration isn't allowed to, the job says
so and BLOCKERS.md has the two-minute fix. Action items that look like
assignments are listed under the lecture with an **Add** button; nothing is
added to Assignments unless you tap it. A failed job can be retried without
transcribing again.

With conversation history on, the summary and the Notion link are also
added to the phone conversation you were in when you started recording.
With failover (FAILOVER.md) a recording stays on the backend it started
on; if that backend goes away mid-lecture, the page says the recording
stopped there.

The audio is deleted as soon as it is transcribed (`[lecture] keep_audio =
true` keeps it), and the transcript and notes once the Notion page exists. Recordings
and job state live in `[lecture] upload_dir` (`data/lectures`; point it at
the `/data` disk on a cloud host). Other settings: `max_upload_mb` (300 per
recording), `chunk_mb` (4), `notes_database_id` (use an existing database
instead of finding/creating one).

### Homework tutor (phone app)
In the **Tutor** tab, take or choose a photo of a problem, add a question
("I got x = -3, where did I go wrong?") or just tap Ask. Claude looks at
the photo and tutors Socratically: hints and questions, checking your
reasoning step by step. It gives the full worked solution only when you tick
"Show the full solution" or ask for it. Equations are drawn with KaTeX
(bundled with the app, no CDN); with "Read explanations aloud" on, the
reply is spoken with the maths read naturally ("x squared over 2"). The
phone shrinks photos to 1600 px before uploading; the PC accepts only JPEG,
PNG or WebP up to `[tutor] max_image_mb` (8) and removes the photo's
metadata (EXIF, including location) before sending it to Claude.

### Study mode (quiz me)
Say or type "quiz me on PHYS 361", "help me study for the midterm" or
"test me on chapter 4", in the chat or in the voice view. Kwil becomes a
Socratic tutor for the rest of the conversation: it asks one question at a
time and waits for your answer, gives a hint (not the answer) when you're
stuck, and after each attempt says whether it was right with a short
explanation, then asks the next one. Questions are short enough to be
spoken, with maths said in words ("x squared over two"); the chat shows
plain text, so the Tutor tab is still the place for drawn equations.

- **Your own material:** with your Canvas course content synced
  (`[canvas] content`), Kwil searches it before the first question and
  quizzes from what it finds; with school deadlines or Notion assignments
  it checks what's coming up for "what's on the midterm". Without them it
  quizzes at a college level from what it knows. (Lecture Notes pages in
  Notion aren't read yet.)
- **Above the text field:** a small "Studying PHYS 361" pill shows while
  it's on; its x turns it off. When it's off, **+** then **Study mode**
  turns it on (Kwil then asks what to study). "Stop studying" or "I'm done" works too.
  A change applies from your next message.
- **What you missed:** each wrong or half-right answer is noted (the
  concept in a few words, its course, how often, when last). Say "review
  what I missed" (or "...in PHYS 361") to be quizzed on those first, most
  missed first; a course is matched ignoring spaces and case ("PHYS361"),
  and one with nothing noted says so instead of quizzing other courses.
  While memory is paused the list isn't used either. Settings -> **Memory** -> **Study: things to review** lists
  them with **Delete** (asks first); **Forget all** clears them too.
  At most 300 are kept per person (the one seen longest ago goes first),
  120 characters each. They are kept in your memory file (`[memory]
  local_path`; an invited person's own `<[family] dir>/<id>/memory.sqlite`),
  so they are in the backup and "Download my data" and deleted with the
  account. Incognito and "Pause memory" note nothing. On a Pi/PC/cloud
  setup each backend keeps its own list, like memory.
- **Model:** while study mode is on every turn goes to the main model (the
  router never sends a tutor's turn to the fast one).
- **Prompt cache:** study mode's rules are a second block at the end of
  the system prompt, switched only between turns, so turning it on or off
  costs one partial cache miss and the turns in between none.
- **Settings:** `[study] enabled` (on by default) turns the tools and the
  header button off.

### Projects (one per class)
Like Projects in ChatGPT and Claude: a space per class (PHYS 361, CS 171,
...) with its own **instructions** ("PHYS 361: Classical Mechanics, use
Taylor's notation, exams are closed-book"), its own **files** (syllabus,
notes, handouts), its own **memories** and its own **chats**. Settings ->
**Projects** lists them; **New project** names one, and **Suggested from
your courses** offers your Canvas courses, the courses of your school
deadlines and (for you, the owner) your Notion Assignments' Course options:
**Create** makes one in a tap (tapping again opens the same one).

- **A project's page:** **New chat in this project**, its instructions
  (Save; they apply from the next chat in it), its files (**Add file**: a
  PDF or a text/Markdown file, checked like a chat attachment: the type
  from the file's bytes, at most 100 pages; photos go with a message
  instead; **Delete** asks first), what Kwil remembered there (**Delete**
  asks), its chats (tap one to read or continue it), **Rename** and
  **Delete project** (asks; its files and memories go, its chats stay in
  History, in no project).
- **Above the text field** the project's name shows while a chat is in one: tap it
  for the project's page, its x leaves the project (your next message
  starts a chat in none). Continuing a chat from History brings its
  project with it; so do "try again", "edit" and "Branch from here".
- **In a project's chat** Kwil follows its instructions, knows its
  memories and reads its files. Your general memories and "Your
  instructions" still apply everywhere; a project's memories are loaded
  only in that project's chats, never anywhere else. "Remember..." in a
  project saves to that project; "remember, in general, ..." saves it for
  every chat.
- **Files:** while a project's files fit about `[projects] inline_tokens`
  (40,000) tokens and `inline_mb` (4 MB), they are sent in full with every
  request of its chats (cached, so later turns are cheap); past that Kwil
  searches them (`search_project_files`, `read_project_file`) instead.
  Bounds: `max_files` (20) per project, `max_file_mb` (10, never over
  `[webapp] max_upload_mb`: 4 on Render) each, `max_person_mb` (50; 20 on
  Render) for all of one person's project files; at most 30 projects and
  100 memories per project; a PDF at most 100 pages. The estimate counts
  2,000 tokens a PDF page and, for text, about a token per three bytes (so
  Chinese counts about a token a character); a PDF whose page count can't
  be read is always searched. If Claude still says the files are too
  long, that chat switches to searching them by itself.
- **Outside text:** a handout or a PDF is usually written by someone else,
  so a chat with project files in it asks first before changing anything
  (as with a chat attachment), and what it remembers there is marked
  "Outside source". The instructions you type never do that.
- **History:** History's search sees every chat; `?project=` filters by
  one (the project page lists its chats). In a project's chat, "what did we
  cover in this class last week?" searches only that project's chats.
- **Where it's kept:** in your memory file (`[memory] local_path`; an
  invited person's own `<[family] dir>/<id>/memory.sqlite`), so each
  person has their own projects (nobody can open anyone else's), they are
  in an invited person's "Download my data" (with the files themselves
  under `projects/`, on top of its size cap) and deleted with the account.
  The owner's backup has every project, its instructions and memories,
  but not the files themselves (keep your originals): with them it would
  be everyone's `max_person_mb` together, over `backup_max_mb`. With
  `[memory] backend = "pinecone"`, a project chat's summary is recalled
  only in that project's chats.
  Deletes are real (overwritten, not just unlinked). Each backend
  (Pi/PC/cloud) keeps its own projects, like memory. Never the Anthropic
  Files API (it belongs to one workspace, not one person).
- **Settings:** `[projects] enabled` (on by default; needs `[memory]
  enabled`).

### Student deadlines (Canvas)
"What's due this week?", "when is my physics midterm?", "add my lab report,
due Friday at 5". Deadlines (exams, assignments, quizzes, classes) are kept
per person, captured from Canvas, from Canvas's notification emails, or
said by hand, and the phone menu's **School deadlines** card shows "What I
captured" with **Looks right**, **This date is right** / **Not this one**
(when two sources disagree on a date) and **Delete**. Four tools:
`list_deadlines`, `confirm_deadline`, `add_deadline` and `delete_deadline`
(delete always asks first; adding or confirming asks too once the
conversation holds captured text, which is written by other people).

Connecting Canvas never uses a school password. Either:

- **Calendar feed link** (simplest): in Canvas open **Calendar** ->
  **Calendar Feed** and copy the link
  (`https://<school>/feeds/calendars/user_….ics`). The link itself is the
  secret (anyone with it can read your Canvas calendar): treat it like a
  password.
- **Access token**: Canvas -> **Account** -> **Settings** -> **New access
  token**, plus your school's Canvas address. It reads the planner
  (assignments, quizzes, discussions, calendar events) and, with `[canvas]
  pages` on, the pages in your courses' modules, where Claude picks out the
  dates.

Everyone, you (the owner) included, connects in the card: type your
school's name and tap **Find my school**, and pick it. **Kwil then picks
the way in by itself** and shows only that one step:

| Your school's students' tokens | Desktop Chrome or Edge | Phone, tablet, other browsers |
| --- | --- | --- |
| allowed | access token | access token |
| blocked | Canvas helper | calendar feed (plus a pointer to the helper on a computer) |
| not known yet | Canvas helper | calendar feed, with **Get course files too** (the token step) |

- **Calendar feed:** tap **Open my Canvas calendar** (Canvas opens in a
  new tab), tap **Calendar Feed** (bottom right) and copy the link, come
  back and tap **Paste** (or paste it by hand), then **Connect Canvas**.
  Paste only fills the field (the phone may ask to allow it; if you say
  no, paste by hand); nothing connects until you tap Connect.
- **Access token:** tap **Open my Canvas settings** (your Canvas's
  settings page opens in a new tab), tap **New access token**, pick an
  expiry date, copy the token, paste it and tap **Connect with token**.
  Since 2025 a student's token lasts at most 120 days; when it runs out the
  sync says to reconnect. If your Canvas has no **New access token**
  button, tap **My Canvas has no New access token button**: Kwil notes
  that your school blocks them and shows the other way at once.
- **Canvas helper:** see below. **Use the calendar feed instead** is
  always one tap away under the token and helper steps.

Kwil knows a school's policy from `kwil/data/canvas_schools.json` (schools
whose own page says so, each with its source and the date it was checked;
empty until entries are verified, and adding one is a data change only)
and from what this server learned (`canvas_policy.json` in `[student]
dir`: per Canvas host, counts and dates only, at most 1000 hosts): the
first sync of a token just connected in the app, the helper reading your
Canvas settings page, or someone's "no New access token button", each
person's word counted once. The newest day's outcome wins (a same-day tie
is "blocked"), and something learned after the school's entry was checked
beats the entry (policies change); an older one never does. Until two
separate events back a learned result, only the person whose own Canvas
it is sees it (so nobody can tell from the card which school another
person on your server uses). Kwil never makes a token for you. `[canvas] tokens_for` (default
`"everyone"`) decides who may use a token at all: with `"owner"`, invited
people are only ever offered the helper or the feed, and connecting with a
token answers them 403 (Canvas's API policy asks apps used by many people
to use OAuth rather than their personal tokens); a member already
connected with a token keeps working until they disconnect. The link is stored encrypted with `USER_DATA_KEY` (without it
the server says it can't store sign-ins): yours in `[student] dir` as
`canvas.enc`, an invited person's in their own folder. It is deleted on
**Disconnect Canvas** (which can also delete what came from Canvas) and,
for an invited person, with their account. Canvas has no way to revoke a
feed link; after disconnecting a token, also delete it in Canvas ->
**Account** -> **Settings**.

Older setups put the owner's link in the environment instead:
`CANVAS_ICS_URL`, or `CANVAS_TOKEN` with `[canvas] base_url` (or
`CANVAS_BASE_URL`), in `.env` (on Render: Environment). While either is
set, the environment wins: the card shows the connection with no form
(connect answers 409). **Disconnect** shows only if the app still holds
an older link of yours; it deletes that file and never touches the
environment (with no such file, disconnect answers 409). To move to the app,
delete `CANVAS_ICS_URL` / `CANVAS_TOKEN` (and `CANVAS_BASE_URL`) from
`.env` or Render's Environment (the server reads them on every request),
then paste the link in the card. **Sync now** fetches everything at most
every `[student] sync_minutes` (15) per person. With `[student] gmail` on
and Gmail connected, Canvas's notification emails from the last two weeks
are read too (read-only; each email once), with `[student] extract_model`
(default: the fast model). Each person's own Claude key is used for that
when they have one.

Re-syncing never duplicates anything. A date you confirmed is never
changed by a sync (the card still shows what Canvas says now), a deleted
item doesn't come back, and items more than 60 days past are dropped.
Your deadlines are in `[student] dir` (`data/student`; Render
`/data/student`); an invited person's are in their own folder under
`[family] dir`, in their "Download my data" zip, and deleted with their
account. Limits: `[student] max_items` (2000) per person and
`max_per_source` (500) per source. `[student] enabled = false` turns it
all off.

**Background sync.** Once Canvas is connected nobody has to tap anything:
the server syncs everyone connected every `[canvas] auto_sync_hours` (6;
`0` = only **Sync now**), and right after you connect. On a failover setup
your own sync runs only on the machine that leads (Pi, then PC, then the
cloud), so your Canvas and Claude are read once. A side-port check copy
never syncs.

**Course files** (with an access token; `[canvas] content`, on by
default). Each active course's syllabus, pages, assignment descriptions,
announcements, module list and files (PDF, Word, PowerPoint, HTML and
text; never images, video, audio, spreadsheets or zips, and never grades
or submissions) are kept as text you can ask about: "what's the late
policy in physics?", "what does the week 5 reading cover?". Two read-only
tools: `search_course_files` (up to 8 snippets) and `read_course_file`
(6000 characters at a time). Their text is written by teachers and others,
so it counts like an email: after a search, adding or confirming a
deadline asks out loud first. Dates found in the syllabus, pages,
announcements and schedule-like files become **unconfirmed drafts** (source
"Course files") in your deadline list, each text read by Claude once.
Only the text is stored, never the files, in `coursefiles.sqlite` next to
your deadlines (an invited person's in their own folder, in their
"Download my data" zip, deleted with their account). **Disconnect Canvas**
always deletes it all; `POST /api/student/courses/<id>/delete` deletes one
course and keeps it out of later syncs. A file is downloaded again only
when Canvas changes it. Limits: `[canvas] max_files` (40) downloads per
sync (the rest come in the next one, half an hour later), `max_file_mb`
(20; Render: 4) per file (bigger files are listed, not read),
`max_course_mb` (20) and `max_person_mb` (100) of text, and
`content_seconds` (300) per sync, apart from the planner's time. Locked
or hidden files, and a course that hides its Files tab, are skipped. The
token is sent to your school's Canvas only, never to the file store
Canvas redirects downloads to. Reading course files needs `pypdf` (in
`requirements.txt`: update with `--requirements`). Kwil reads only your
own courses, with your own token, like opening the pages yourself; some
schools' rules limit automated access, so check yours. It is read-only
and rate-limited.

**Canvas helper** (for schools that block access tokens; `[canvas]
helper`, on by default, needs `[canvas] content`). The Kwil Canvas helper,
a Chrome/Edge extension in `tools/canvas-helper/` (load it unpacked: see
its README; an iOS Shortcut comes later), reads
your courses in your own signed-in Canvas tab and sends the same content
to Kwil, which stores it exactly as the token sync does (same limits,
same drafts, same tools, same deletes). Setup is one tap: **Set up Canvas
helper** in the School deadlines card shows a one-time pairing code for
10 minutes; the helper, open in the same browser, picks it up by itself
and trades it for its own **sync key** (shown to the helper once, never
to the page). The key opens only the helper's two push routes; only its
SHA-256 is kept (`canvas_helper.json` in `[student] dir`, or in an invited
person's own folder). **Revoke helper** ends it (what it sent stays until
you disconnect); **Disconnect Canvas** ends it and deletes the content;
removing a person ends theirs. Signing out does not: the helper has its
own key, so revoke it separately. Pairing again replaces the old key.
Wrong keys and codes count toward the sign-in lockout, and a key gets 120
pushes an hour. Kwil never fetches anything for the helper and never sees
your Canvas sign-in or cookies: the helper sends only the courses' JSON
and file bytes it read. Pair it from the Kwil address the helper talks to
(the cloud app, `heykwil.com`): what it sends is stored on that server.
The extension finds your Canvas by itself on `*.instructure.com`; for a
school domain of its own, click its icon there and **Use as my school's
Canvas** (Chrome asks for that one site; a Kwil on its own domain is
**This is my Kwil**, which asks "Send your Canvas courses to <host>?"
first, and a second Kwil needs **Switch to <host>**). It syncs about half a minute after
pairing, then every 6 hours while the browser is open and you are signed
in to Canvas, pushing as it reads, and pushes only the files Kwil asks
for (at most 40 a run). Each run it also reads your Canvas settings page
once (a GET, the page with Canvas's **New access token** button) to see
whether your account may make a token, and tells Kwil yes, no or nothing
(so Kwil learns your school's policy); it never makes a token.
Its popup shows only status (paired, the Kwil and Canvas it uses, the
last sync) with **Unpair this browser** and **Revoke in Kwil**. It asks
for `storage`, `alarms`, `activeTab`, `scripting` and the hosts
`heykwil.com`, `*.instructure.com` and `*.inscloudgate.net` (Canvas's
file store); never `<all_urls>` or cookies.
The helper reads only your own courses, with your own sign-in, like
opening the pages yourself; some schools' rules limit automated access,
so check yours. It is read-only and rate-limited.

### Study briefings (notifications)
The phone app can speak up first: a notification in the morning with
what's due today and tomorrow, in the evening with what's due tomorrow, a
look at the week on Sunday, exam reminders (14, 7, 3 and 1 day out, while
no review is planned), new deadlines it captured, and a "how did class
go?" after a class. Tapping one opens the brief in the app with a
**Play** button (it never plays by itself).

- **Turn it on:** Settings -> **Enable notifications**, then allow them.
  On an iPhone this works only in the app added to the Home Screen
  (iOS 16.4 or later); in Safari the menu says to add it first. Tap
  **Turn off notifications** to stop (that phone only).
- **Needs** `USER_DATA_KEY` on the server (subscriptions are stored
  encrypted; without it the button says the server can't store them yet)
  and deadlines to talk about: the student deadline store (Canvas sync,
  screenshots); without one nothing is ever sent. The server makes its
  own VAPID key once (`[push] owner_dir`, Render: `/data/student`), or set
  `VAPID_PRIVATE_KEY` (a raw P-256 key in base64url) in `.env`. A new key
  means tapping Enable again on every phone. For Apple, set `[push]
  subject` to `mailto:you@example.com` unless `[webapp] public_origin` is
  an https address.
- **How much:** at most `[proactive] max_per_day` (2) a day, none in
  quiet hours (`quiet_start` 22:00 to `quiet_end` 07:30, in each person's
  own time zone), and rules due at the same time go as one notification.
  The times are `morning`, `evening`, `weekly_day` and `weekly_time`;
  `check_minutes` is how often it looks; `exam_days` and `class_minutes`
  tune the exam and class rules.
- **Claude** is asked only when a rule fires: one short message
  (`[proactive] model`, default the fast model) from the course, title,
  kind and due date only; no page or email text, no grades, no tools. A
  beta tester without their own key, or a failed call, gets a plain
  template instead. Opening the brief never calls Claude.
- On a Pi/PC/cloud setup, each backend keeps the subscriptions made
  through it and sends to them itself, whichever backend leads; a phone
  saved on two backends still gets each briefing once (a claim in the
  shared history log). If the backend a phone subscribed through is down
  at briefing time, that phone misses it, so enable notifications from
  the backend that is normally in use.

### Reminders and scheduled tasks
Ask Kwil to remind you, and the reminder arrives as a notification on
your phone (the same notifications as the study briefings, so turn them
on first: Settings -> **Enable notifications**).

- "Remind me at 9 to submit HW7", "remind me in 20 minutes to stretch",
  "remind me 30 minutes before tennis" (Kwil looks the event up first).
  Kwil says the time back in your own time zone ("I'll remind you
  Thursday at 9:00 PM").
- Repeats: "every Monday at 8am...", "every weekday at 7:30". Daily or
  weekly, on any days, at up to 24 times a day; no "every other week" or
  end date yet. A repeat keeps its clock time across daylight saving
  (9:00 stays 9:00); a time that doesn't exist that night (2:30 when the
  clocks go forward) comes at 3:30.
- **Scheduled tasks:** "every Monday at 8, tell me what's due this week".
  At that time Kwil runs the request with read-only tools only (your
  calendar, school deadlines, Notion assignments, memories; never the web
  and never anything that changes something), in a fresh conversation
  that isn't saved as you talking. The answer is the notification (cut to
  its size) and the whole answer is in History under the task's name
  (unless `[history] incognito` is on). It counts against an invited
  person's daily limits and uses their own key. At most `[reminders]
  max_tasks_per_day` (48) run per person in a day, you included; past
  that a task is skipped and you get one "Skipped: too many scheduled
  tasks today" notification that day.
- **See and change them:** Settings -> **Reminders** lists the active ones
  with their next time and repeat, with **Pause** / **Resume** and
  **Delete** (asks first); tapping a reminder notification opens it. By
  voice: "what reminders do I have?", "move the HW7 reminder to 10",
  "cancel the gym reminder" (Kwil asks before cancelling).
- **Quiet hours** (`[proactive] quiet_start`-`quiet_end`) only hold back
  a reminder whose time you didn't say ("remind me tomorrow to..."); it
  comes when they end. A time you said ("at 6am") is kept.
- **If the server was down:** a reminder at most `[reminders]
  grace_minutes` (60) late comes once when it is back; an older one is
  listed as Missed (a repeat goes on from its next time).
- **Outside text:** a reminder set while a web page, email or calendar
  invite is in the conversation asks you first, is marked Outside source, and
  its notification's title says "(from outside text)".
- **Limits:** 100 active reminders and 200 notifications a day per person,
  300 characters each. Each person (family members too) has their own,
  in their own time zone: `[reminders] path` for yours
  (`data/reminders.sqlite`, Render: `/data/reminders.sqlite`), and
  `<[family] dir>/<id>/reminders.sqlite` for invited people. They are in
  the backup, an invited person's "Download my data", and deleted with
  their account.
- **Settings:** `[reminders] enabled`, `check_seconds` (how often due
  reminders are looked for, 60) and `prompt_tasks` (scheduled tasks on or
  off). On a Pi/PC/cloud setup each backend keeps its own reminders (like
  memory); set them through the backend that is normally in use.

### Research (deep dives)
Ask for research and Kwil works on it in the background, then sends a
notification when the report is ready (turn notifications on first:
Settings -> **Enable notifications**). It runs in the web app (the phone
app, typed or by voice, on the PC/Pi host or the cloud); the PC's own
spoken mode (`python -m kwil`) doesn't have it.

- "Research the best internships for physics majors this summer", "do a
  deep dive on lithium-sulfur batteries". Kwil asks first, with how long
  it takes and how many searches it may do ("Start research on: ...? It
  takes about 5 to 10 minutes and does up to 10 web searches"), then
  answers at once; say "a deep one" for the larger budget (about 10 to 20
  minutes, up to 25 searches).
- **What it does:** plans an outline (up to 3 parts, 5 for a deep one),
  researches each part with Anthropic's web search and web fetch, then
  writes a Markdown report: a short summary first, then sections, with
  numbered sources (the same sources list and superscripts as a web
  answer in the chat). It uses the main model with adaptive thinking at
  `[research] effort` ("high").
- **While it runs** a small card above the message box says
  "Researching… (3/10 searches)", with **Cancel** (asks first; what it
  found so far isn't kept). "Is my research done?" works by voice too.
- **When it's ready:** the notification "Your research on ... is ready"
  opens the report. It is a conversation in History titled "Research:
  <question>", with your question and the report and its sources: open,
  continue, export or delete it like any other. Continuing it counts as
  outside text (web pages), so Kwil asks before changing anything. The
  card's **Save to Notion** (only when your Notion is connected) files it
  in a "Research" database next to your Assignments, once per report.
- **Cost and limits:** web search costs about $10 per 1000 searches plus
  Claude's tokens. One job runs at a time per person, at most
  `[research] per_day` (3) a day each (you too), up to
  `standard_searches` / `deep_searches` searches and `standard_fetches` /
  `deep_fetches` pages per job. Each job also has a time and token budget
  (`standard_minutes` / `standard_tokens`, `deep_minutes` / `deep_tokens`);
  past it (or if a search step fails) Kwil writes the report from what it
  found and says it is partial. Invited people's research runs on their own key if they saved
  one; a family member without one uses yours (as their chats do), so
  their research is on your bill, within the same 3 a day per person; a
  beta tester without one is told to add it.
- **What the research can see:** only the question, exactly as you
  confirmed it (Kwil shows it whole before it starts). None of your
  memories, instructions, projects, files, history, calendar or email is
  sent with it, and it has no tool but web search and fetch, so nothing
  it reads on the web can change anything or slip your data into a
  search. Put everything it needs in the question.
- **Needs** `[web] enabled` with web search on and History on (the report
  is saved there; it doesn't run in an incognito conversation). Job
  files (the question, counts and times, no report) are kept for 2 days
  in `[research] dir` (`data/research`, Render: `/data/research`) and an
  invited person's `<[family] dir>/<id>/research/`. A job cut off by a
  restart isn't run again (it would pay for its searches twice); its
  card says so. On a Pi/PC/cloud setup the job runs on the backend you
  asked, once.

### Today (one look at your day)
The **Today** tab at the bottom of the phone app (on a wider screen, the
calendar button at the top; or the link `/#today`) opens
**Today**: a read-only screen with, in order, your study brief, today's
and tomorrow's events (your primary Google calendar), what's due by
tomorrow and what's overdue (school deadlines from the last two weeks,
and Notion assignments), today's reminders, your latest research (up to
3) and the study-mode concepts you missed most (up to 5). Everyone signed
in sees their own: the owner's, or an invited person's own calendar,
deadlines, reminders, research and study list.

- **Each row opens its place:** the brief opens the brief, a reminder
  opens Settings, Reminders, a deadline opens its connection in Settings,
  a finished research report opens its conversation. Events and study
  items have **Ask Kwil**: it starts a new chat with a question in the
  message box ("Tell me about “Dentist” at 3:00 PM today.") and waits for you
  to send it; it never sends by itself.
- **Outside titles stay out of the question.** Events and study items
  have Ask Kwil; for an invite, or a study item saved while outside text
  was in the chat, the question names it by time or list position, never
  its title ("What's my 3:00 PM event today about?"), so someone else's
  wording never goes to Kwil as if you had typed it.
- **Read-only and fresh:** opening the screen reads everything once
  (Google Calendar and Notion are read live, side by side, about 8
  seconds at most for both); opening it again while it loads waits for
  the same answer, and more than 20 opens a minute are refused for a
  minute; nothing
  is written, nothing is cached, and it doesn't refresh in the background.
  A section that fails says so on its own line; the rest still show. Not
  connected to Google: "Connect Google" opens it in Settings; a refused
  sign-in says "Reconnect Google". Offline: an error line and **Retry**.
- No setting: it is there for everyone signed in.

## Smart home (Home Assistant)

"Turn off the bedroom lights", "set the living room light to 50%", "is the
front door locked?", "set the thermostat to 70", "turn on the fan". Off
by default; it needs a running [Home Assistant](https://www.home-assistant.io/)
on your network:

1. In Home Assistant, create a dedicated user for the assistant
   (Settings -> People -> Add person, allow login, **not** an
   administrator), sign in as that user, then profile -> Security ->
   Long-lived access tokens -> Create token. Copy it once. A non-admin
   token can still switch devices but can't change Home Assistant's
   settings; it can't read rooms, so the assistant then matches rooms by
   device name ("Bedroom Lamp").
2. In `.env`: `HA_URL=http://homeassistant.local:8123` (your address) and
   `HA_TOKEN=<the token>`. The token is masked in the log.
3. In `config.local.toml`:

```toml
[home_assistant]
enabled = true
```

Restart; the startup line says so if the URL or token is missing.
Claude gets three tools: `ha_list` (devices with their state, by room or
kind, cached for `[home_assistant] cache_seconds`), `ha_state` (one
device) and `ha_control`. Spoken names are matched to Home Assistant's
friendly names and areas, loosely ("kitchen lite" still finds the
kitchen light); if two devices fit ("the lamp"), it asks which one.

Safety, in code rather than in the prompt:
- Only the kinds of device in `[home_assistant] domains` (lights,
  switches, fans, climate, covers, media players, sensors and locks by
  default) are listed or touched, and only a fixed set of actions is
  called (on, off, brightness, colour temperature, thermostat, fan speed,
  open/close, play/pause/volume, lock/unlock). There is no way to call
  any other service: no restart, no arbitrary scripts. A script, scene or
  automation runs only if you list its entity id in `[home_assistant]
  scripts`.
- Locks, alarm panels, garage doors and gates (by Home Assistant's device
  class, or a switch or cover with garage, gate, door or lock in its name,
  even run together as in "Garagedoor", but not indoor, outdoor, doorbell
  or clock; deadbolt; in Chinese 车库, 锁, 门禁, 大门, 闸门 or 门 ending
  the name or a word), plus anything in `[home_assistant]
  secure_entities` (put a relay there if its name says none of these: a
  room called "Garage" doesn't count, nor non-English words like "Tor"), always ask for a spoken yes first, are never switched
  for a whole room at once, and are only found by their exact name or all
  of its words (never by a similar spelling).
- Unlocking or opening one of those is refused outright while a web page
  or email is in the conversation (start a new one). Other changes ask
  first in that case, as everywhere else.
- Home Assistant is Kyle's alone: family members never get these tools.
  The Render cloud host can't reach a home network: on the cloud, connect
  it from the app with its public address instead of `HA_URL`/`HA_TOKEN`
  ("Connect your services in the app (cloud)"). Then the same tools and
  rules apply with `[home_assistant]`'s domains, scripts and
  secure_entities, even with `enabled = false` (connecting is the opt-in).

**Plain http.** Home Assistant usually serves http on the LAN, so the
token travels unencrypted. That is fine inside your home network or over
Tailscale; anywhere else use https (Nabu Casa or your own certificate).
The assistant warns at startup when `HA_URL` is a plain http address
outside the home network.

## Team chat (Slack, Discord, GitHub)

"What happened in #eng today?", "summarize the release thread", "any
decisions about the launch in Discord this week?", "what's new in the
issues on hey-claude?", "read the spec file Bob shared". Claude reads the
channel, thread, search results or a shared text file and sums up the
ideas, decisions, open questions and files. It never posts: there is no
tool that writes to Slack, Discord or GitHub. "Save that as a digest"
files Claude's summary as a page in your Notion (a "Chat Digests"
database it creates beside Assignments, or `[teamchat]
digest_database_id`), after a spoken yes, once per service, channel and
day (asking again that day finds the same page and adds nothing). Off by
default; owner only (family members never get these tools).

LinkedIn isn't here: LinkedIn doesn't allow apps to read your messages or
feed.

Set up the services you use (each token is read only and goes in `.env`):

1. **Slack** (a user token, so it reads what you can read): at
   api.slack.com/apps, Create New App -> From scratch, in your team's
   workspace. Under OAuth & Permissions -> User Token Scopes add
   `channels:read`, `channels:history`, `groups:read`, `groups:history`,
   `users:read`, `files:read` and `search:read` (no direct-message
   scopes: DMs are never passed to Claude). Install to Workspace, then copy the
   User OAuth Token (`xoxp-...`) into `.env` as `SLACK_USER_TOKEN=`.
   **Never turn on Manage Distribution:** a distributed app that isn't in
   the Slack Marketplace is limited to one history read a minute.
2. **Discord** (a bot that a server admin adds): at
   discord.com/developers/applications, New Application -> Bot -> Reset
   Token, and copy it into `.env` as `DISCORD_BOT_TOKEN=`. On the same
   page turn **on** Message Content Intent (without it Discord sends
   messages with no text; the assistant says so) and turn **off** Public
   Bot. Under OAuth2 -> URL Generator tick `bot` with View Channels and
   Read Message History (permissions=66560) and have a server admin open
   that link to add the bot.
3. **GitHub**: github.com/settings/personal-access-tokens -> Generate new
   token (fine-grained), only the repositories you want, with read-only
   Issues, Pull requests, Discussions and Metadata. Put it in `.env` as
   `GITHUB_TOKEN=`.
4. In `config.local.toml`, turn it on with the services you set up:

```toml
[teamchat]
enabled = true
slack = true
discord = false
github = true
channels = ["#eng", "general"]
github_repos = ["kylechen839-max/Kwil"]
```

Restart; the startup line names any turned-on service whose token is
missing. Claude gets `chat_channels`, `chat_read` (a channel or one
thread, the last 24 hours by default, or a day such as "yesterday"),
`chat_search`, `chat_files`, `chat_read_file` and, with Notion set up,
`chat_save_digest`.

Limits, in code:
- `[teamchat] channels` lists the Slack and Discord channels it may read
  (by name or id, Discord also as server/channel); `[]` means every
  channel the token can see. `[teamchat] github_repos` does the same for
  GitHub. Slack DMs and group DMs are never passed to Claude or logged
  (Slack's search and file list return some; they are dropped). A search
  that had to be cut (Discord: 20 channels in each of 3 servers; GitHub:
  5 repos) says so; name a channel or repo to search it.
- At most `[teamchat] max_messages` (100) messages per read, 300
  characters of each, about 12,000 characters per answer, 20 files per
  list, and only text files up to 50 KB, downloaded only from Slack's or
  Discord's own file servers (the Slack token goes to files.slack.com
  only; Discord's file links need none).
- Everything read is other people's text, so once Claude has read chat,
  any tool that changes something asks you first. Message text, file
  text and tokens never go in the log.
- After a refused token it stops calling that service until restart, and
  when a service asks it to slow down it waits that long without calling
  (Discord bans addresses that make too many bad requests).
- `[teamchat] timeout` is the seconds per request.

## Spotify by voice

"Play my study playlist", "play some lo-fi", "pause", "skip", "go back",
"what's playing?", "turn it up", "play it on my phone". The assistant
remote-controls a Spotify app that is already open somewhere (the PC's
desktop app, your phone, a speaker) through the Spotify Web API; it plays
no audio itself. Your own playlists are searched first, then Spotify's
catalog (a mood or genre plays a playlist, a song name plays that song).

- **Spotify Premium is required.** Spotify only lets apps control playback
  on Premium accounts; without it every command answers "Spotify only lets
  apps control playback with a Premium account."
- **Spotify has to be open on a device.** With nothing playing anywhere it
  uses this computer's Spotify if it is open, or the only device that is;
  with several it asks which one.
- **Nothing changes your library.** Sign-in asks only for
  user-read-playback-state, user-modify-playback-state and
  playlist-read-private: no tool can edit
  playlists, saved songs or follows. Playback control never asks for a
  yes (it is harmless and easy to undo).
- **Pauses while you talk.** With `[spotify] pause_while_listening = true`
  (the default) the wake word pauses Spotify if it is playing on this
  computer, and it resumes when the assistant goes back to waiting for
  "Hey Claude" (not if you said "pause" meanwhile). This computer is the
  Spotify device whose name is `[spotify] local_device`, or the computer's
  hostname when that is empty. Only the wake-word mode does this; the phone
  app plays its replies on the phone.
- Family members (cloud invites) never get Spotify: not in this version.
  Spotify is meant for the PC or Pi (wake word and its phone web app);
  the cloud server's sign-in wall doesn't pass Spotify's redirect back, so
  connect it on the PC.

Setup, once (about 5 minutes):

1. At developer.spotify.com -> Dashboard -> Create app (any name, "Web
   API"). Under Redirect URIs add exactly `http://127.0.0.1:8888/callback`
   (the `[spotify] redirect_uri` default), and, for the phone app's button,
   `<public_origin>/spotify/callback` too (e.g.
   `https://your-pc.your-tailnet.ts.net/spotify/callback`). In Development
   mode, add your Spotify account's email under User Management.
2. Copy the app's **Client ID** into `.env` as `SPOTIFY_CLIENT_ID=...`. No
   client secret is used (the sign-in is PKCE).
3. Turn it on in `config.local.toml`:

   ```toml
   [spotify]
   enabled = true
   ```
4. Sign in, either on the PC or Pi:

   ```powershell
   python -m kwil.tools.spotify
   ```

   It prints a link (and opens it); after you allow access the browser
   lands on a 127.0.0.1 address. If that page doesn't load (for example
   the link was opened on another device), that's fine: copy the full
   address from the address bar and paste it back. Or, in the phone app,
   gear -> **Connect Spotify** (owner only; needs `[webapp] public_origin`).
5. Restart the assistant. The sign-in is saved privately in
   `[spotify] token` (`data/spotify_token.json`); delete that file to
   disconnect, and remove the app's access at spotify.com -> Account ->
   Manage apps.

## Raspberry Pi (Pi 4/5, 64-bit Raspberry Pi OS Bookworm)

The code is the same as on Windows; two scripts do the Pi-specific setup.

### 1. Install

```bash
git clone <this repo> kwil && cd kwil
./scripts/install_pi.sh
```

The script is safe to re-run. It apt-installs the system packages
(`python3-venv python3-dev libportaudio2 espeak-ng libespeak1 alsa-utils`),
creates `.venv`, installs `requirements.txt` plus openWakeWord (with
`--no-deps`, because its Linux metadata wants `tflite-runtime`, which has no
wheels for newer Pythons; only the ONNX backend is used), creates `.env` from
`.env.example` if it doesn't exist, and pre-downloads the Whisper and
wake-word models. `SKIP_APT=1`, `SKIP_MODELS=1` and `PYTHON=python3.11`
change those steps.

### 2. Copy your keys and files from the PC

From the project folder on your PC (PowerShell; replace `raspberrypi` with
the Pi's host name or IP):

```powershell
scp .env credentials.json token.json pi@raspberrypi:~/kwil/
scp -r voices pi@raspberrypi:~/kwil/   # the Piper voice, if you have it
```

Or type the keys into `nano .env` on the Pi. The "Hey Claude" wake-word
model (`keywords/hey_claude.onnx`) is in the repo and works on both
(openWakeWord models are not platform-specific), so there's nothing to copy
for it.

**Google sign-in without a browser:** if you don't copy `token.json`, run
`.venv/bin/python -m kwil.tools.calendar --headless` on the Pi. Open the
printed link on your phone or PC, sign in, then paste back the
`localhost:8765/...` address the browser ends up on (the page itself will
fail to load; that's expected). This needs `credentials.json` on the Pi.

### 3. Check, try, and run at boot

```bash
.venv/bin/python -m kwil --check   # keys set?, files, mic/speaker, models
.venv/bin/python -m kwil           # try it; Ctrl+C to stop
./scripts/install_service.sh            # start at boot (systemd)
```

`--check` prints which keys are set (never their values), whether
`credentials.json`, `token.json` and the wake-word model exist, the audio
devices with their index numbers, whether the Whisper model is cached, and,
which voice will speak (Kokoro, Piper or pyttsx3).
It exits with 1 if something required is missing.

`install_service.sh` installs `deploy/kwil.service` as a systemd
**user** service (the Pi's sound server runs in your user session, which a
system service can't reach) and turns on "lingering" so it starts at boot
without logging in. It waits for the network, restarts on failure, and logs
to the journal as well as `logs/assistant.log`:

```bash
systemctl --user status kwil
journalctl --user -u kwil -f
systemctl --user restart kwil     # after changing .env or config.toml
./scripts/install_service.sh --uninstall
```

If the Pi also serves the phone web app, `./scripts/install_watch.sh` adds
the watchdog (see "Health checks and safe updates"); if it runs only the
wake word, set `[watch] enabled = false` in `config.local.toml` there.

**Voice:** Piper sounds much better than eSpeak and runs well on a Pi 4/5
(the `piper-tts` wheel for 64-bit ARM ships its own espeak-ng data). Run
`.venv/bin/python -m kwil --download-voice en_US-lessac-medium` on
the Pi (or copy `voices/` from your PC); `[tts] engine = "auto"` then uses
it (see [Voices](#voices)). Kokoro also installs on a Pi but takes well over
a second per sentence there, so "auto" keeps Piper on ARM. Without a voice
file the assistant falls back to eSpeak.

**Speed:** `install_pi.sh` puts `[stt] model = "base.en"` in
`config.local.toml` (the PC's `small.en` is too slow on a Pi); change it
there to `"tiny.en"` if `base.en` feels slow (a Pi 4), and put any other
`[stt]` setting under that same `[stt]` line (a second `[stt]` header is
invalid TOML). On a Pi with no screen, set `[web] browser = false`.

### Troubleshooting

- **No mic found** (`--check` shows "Input devices: none found"): plug the
  USB mic in before booting or re-run `--check`; `arecord -l` should list
  it. If it's missing there too, it's a hardware/driver problem, not the app.
- **Wrong mic or speaker:** `.venv/bin/python -m sounddevice` (or `--check`)
  lists devices as `[index] name`. Put the index, or part of the name, in
  `config.toml`:
  ```toml
  [wake]
  mic_device = "1"        # or "USB PnP"
  ```
  For the speaker, pick the default output with `sudo raspi-config` ->
  System Options -> Audio, and test it with `speaker-test -t wav -c 2`.
- **ALSA errors** (`ALSA lib pcm.c: ... Unknown PCM cards.pcm.rear` and
  similar) are printed by PortAudio while it probes devices and are usually
  harmless. If audio really fails: check `arecord -l` / `aplay -l`, make sure
  your user is in the `audio` group (`groups`; fix with
  `sudo usermod -aG audio $USER` and log in again), and that nothing else
  holds the device (`fuser -v /dev/snd/*`).
- **`PortAudio library not found`:** `sudo apt install libportaudio2`.
- **No voice from pyttsx3:** `sudo apt install espeak-ng libespeak1`.
- **Service keeps restarting / stopped:** `journalctl --user -u kwil -n 50`
  shows why (most often a missing `ANTHROPIC_API_KEY` in `.env`). After 5
  failures in 5 minutes it stops; fix the cause, then
  `systemctl --user restart kwil`.
- **Service doesn't start at boot:** `loginctl show-user $USER -p Linger`
  must say `Linger=yes` (`sudo loginctl enable-linger $USER`).

## Phone app failover: Pi, then PC, then cloud

The phone app (installed from the cloud host) uses the Raspberry Pi when it
can reach it, the PC when it can't, and the cloud host when neither
answers, and moves back up when a better one returns (never mid-reply).
FAILOVER.md has the whole protocol. To set it up:

1. Run the web app on each machine you want in the list, with the same
   `WEB_TOKEN` in every `.env` (or the Render environment), and expose the Pi
   and PC with `tailscale serve`.
2. In each machine's `config.toml`, fill in `[failover]`: `role` (or pass
   `--role pi` / `--role pc` / `--role cloud`), `app_origin` (the cloud
   URL the phone app is installed from) and the three `backends` URLs:
   ```toml
   [failover]
   role = "pi"
   app_origin = "https://hey-claude.onrender.com"
   backends = [
     { role = "pi", url = "https://pi.tail1234.ts.net" },
     { role = "pc", url = "https://kyle-pc.tail1234.ts.net" },
     { role = "cloud", url = "https://hey-claude.onrender.com" },
   ]
   ```
3. Copy the same `.env`, `credentials.json` and `token.json` to each machine
   (one Google sign-in works on all of them; see FAILOVER.md section 6).
4. On the phone, open the app's Settings -> Backends to check the URLs and
   the badge (Pi / PC / Cloud), or force one backend.

Conversation history is synced between the machines every
`sync_interval` seconds (30). Memory (your instructions and saved
memories) is kept per machine and not synced yet; with `[memory] backend =
"pinecone"` the Pinecone notes are shared as before.
Tests: `pytest` (server side) and `node --test tests/js/*.test.mjs` (the
router; Node 18+).

## Windows PC as the always-on home host

No Pi needed if the PC stays on (see "Do I need a Pi?" in
[PI_MIGRATION.md](PI_MIGRATION.md) for the trade-offs). In PowerShell, in the
project folder:

```powershell
.\scripts\install_windows_service.ps1             # install and start
.\scripts\install_windows_service.ps1 -NoWeb      # wake word only, no phone app
.\scripts\install_windows_service.ps1 -PowerOnly  # just the power check
.\scripts\install_windows_service.ps1 -Uninstall  # stop and remove
```

It registers two Task Scheduler tasks under `Kwil` (no admin rights
needed): **Wake** (`--mode wake`) and **Web** (`--mode web` on
127.0.0.1:8765, the address `tailscale serve` points at; `-Port` changes it).
They start 20 s after you sign in, run hidden, and are kept running by
`python -m kwil.host`, which waits for the network, restarts the
assistant 10 s after a crash (5 minutes after 5 crashes in 5 minutes) and
logs to `logs/host-wake.log` / `logs/host-web.log` (the assistant's own log
is still `logs/assistant.log`). After a reboot they start when you sign in;
for no sign-in at all, turn on automatic sign-in for your account.

**Already have a hand-made task for the web app** (e.g. "Hey Claude Web" in
the Task Scheduler Library, running `python -m kwil --mode web --host
127.0.0.1 --port 8765`)? The script finds it, shows its command and asks
before replacing it with `\Kwil\Web` (type `y`), so two servers never
fight over port 8765. Your `tailscale serve` setup keeps working unchanged
because the port is the same. If you answer no, only the wake-word task is
installed. If something else holds the port, it says what and skips the web
task. `-Uninstall` removes only the `Kwil` tasks and leaves `tailscale
serve` and the power settings as they are.

Then it shows the power plan and offers, after you type `y`, to set **never
sleep** and **never hibernate** when plugged in and to turn off **USB
selective suspend** (which can power down a USB mic). The display may still
turn off. The preflight's `power` row warns if sleep comes back (Windows
updates sometimes reset power plans).

### Automatic updates

**Off by default, and it stays off until you set up signing (below).** With
it on, changes reach the PC (and the phone app it serves) or the Pi by
themselves. Turn it on in `config.local.toml`, a gitignored file next to
`config.toml` for this machine's own settings (so `config.toml` stays
unchanged and never blocks an update):

```toml
[update]
enabled = true
```

What it does: the host supervisor (`python -m kwil.host`, which the
`Kwil` tasks and the Pi's `kwil` service run) checks every
`check_minutes` (15) for a new commit on the `stable` branch. Only the
**promote-stable** workflow moves `stable`, and only to a master commit on
which both `tests` and `pi-arm64` passed. Before any new code runs here:

- `origin` must be this GitHub repository (`[update] repo`);
- the new commit, or a `stable-<sha>` tag on it, must carry a valid SSH
  signature from a key in your **allowed_signers** file, which lives outside
  the checkout so an update can't change whom you trust:
  `%APPDATA%\Kwil\allowed_signers` on Windows,
  `~/.config/kwil/allowed_signers` on Linux/the Pi (a file set up before
  the rename, in `HeyClaude\` or `~/.config/hey-claude/`, is still read
  while the new folder has none). No file, no
  valid signature: no update, and `logs/host-<mode>.log` says why;
- HEAD must be on `master` with no local commits (fast-forward only), and no
  local edits to tracked files. Nothing local is ever reset or discarded;
  anything else is skipped and logged.

Then `git merge --ff-only`, a smoke check (the package imports, `--check`
passes) and a restart: the web app only when no request or voice turn is
running (at most ~2 minutes), and the update only counts once `/healthz`
reports the new version within `health_seconds` (180). If anything fails it
goes back with `git reset --keep`, restarts the old version and never
retries that commit. An update that **changes requirements.txt is never
applied automatically** (pip would install new, unpinned dependency ranges
into the venv the assistant is running from): stop the assistant, then run
`python -m kwil --update`. Everything is logged to
`logs/host-<mode>.log` (failures start with `!!!`), with the exact range
(`old -> new`); `logs/update-state.json` has the last result. With
`[watch] gate` on (the default) an update also has to pass the go-live
check before anything restarts on it (see "Health checks and safe
updates"). Moving HEAD yourself (`git checkout`, `git pull`) restarts the
assistant once the go-live check passed, without the `/healthz` rollback;
use `.\scripts\deploy.ps1` instead of `git pull`. The supervisor itself keeps running its starting version until
the task or service restarts (next sign-in or reboot, or `systemctl --user
restart kwil` on the Pi).

`python -m kwil --update` updates right away (same checks, also with
`enabled = false`). The phone page notices the new version through
`/healthz` and reloads itself once, never in the middle of a turn, a
recording or the voice conversation.

**Setting up signing (once). Recommended: you approve each update** with
a key that exists only on your PC. The workflow moves `stable` after green
CI; nothing installs until you sign that commit.

1. Make the key and let git sign with it (choose a passphrase):
   ```powershell
   ssh-keygen -t ed25519 -C "kyle kwil approvals" -f "$env:USERPROFILE\.ssh\kwil_approve"
   git config gpg.format ssh
   git config user.signingkey "$env:USERPROFILE\.ssh\kwil_approve"
   ```
2. Trust its public half on each machine that updates:
   ```powershell
   $dir = "$env:APPDATA\Kwil"; New-Item -ItemType Directory -Force $dir | Out-Null
   $pub = Get-Content "$env:USERPROFILE\.ssh\kwil_approve.pub"
   [IO.File]::WriteAllText("$dir\allowed_signers", "kylechen839@gmail.com namespaces=`"git`" $pub`n")
   ```
   On the Pi: `mkdir -p ~/.config/kwil` and put the same line in
   `~/.config/kwil/allowed_signers` (only the public key goes there).
3. Each update you approve: look at what changed, then sign and push a tag:
   ```powershell
   git fetch origin stable
   git log --oneline HEAD..origin/stable
   $sha = git rev-parse origin/stable
   git tag -s "stable-$($sha.Substring(0,7))" -m "approved" $sha
   git push origin "stable-$($sha.Substring(0,7))"
   ```
   The hosts update on their next check (`git verify-tag` against
   allowed_signers; only SSH signatures count, never OpenPGP).

**Optional, weaker: CI signs for you.** A CI-signed tag only proves **"CI
passed"**, not **"Kyle approved"**. If you want it anyway, the key must live
in a GitHub **Environment**, never as a repository secret (a repository
secret can be read by a workflow pushed on any branch, which could sign its
own code):

1. GitHub -> the repo -> Settings -> Environments -> New environment
   `stable-signing`: Deployment branches and tags -> Selected -> `master`
   only; Required reviewers -> you.
2. Make a key without a passphrase (`ssh-keygen -t ed25519 -N '""' -C
   "kwil stable" -f "$env:TEMP\stable_signing"`), add the private
   key as the environment secret `STABLE_SIGNING_KEY` of `stable-signing`,
   add the `.pub` line to allowed_signers as in step 2 above (with
   `stable-promotion@users.noreply.github.com`), then delete both files.
3. Each promotion then waits for your approval in GitHub (Actions ->
   promote-stable -> Review deployments) before the `sign` job signs
   `stable-<sha>`. With both keys trusted, either one is enough.

The only way around signatures is `[update] trust_unsigned = true`: then
**anyone who can push to master or stable can run code on this machine**.
Use it only for a repository only you can push to.

**Still running the hand-made "Hey Claude Web" task?** It has no supervisor,
so it never updates. Switch with this, in PowerShell (5.1 or 7):

```powershell
cd C:\Users\kylec\Claude-Personal-Assistant
# 1. Up to date: no changes in `git status` (config.toml edits: move them
#    to config.local.toml first), then
git status --short
git checkout master
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Turn on automatic updates in config.local.toml (UTF-8 without a BOM,
#    which the TOML reader needs); it only updates once signing is set up
$local = Join-Path (Get-Location) 'config.local.toml'
if (Test-Path $local) { $text = [IO.File]::ReadAllText($local) } else { $text = '' }
$nl = [Environment]::NewLine
if ($text -notmatch '(?m)^\[update\]') { $text += $nl + '[update]' + $nl + 'enabled = true' + $nl }
[IO.File]::WriteAllText($local, $text, (New-Object Text.UTF8Encoding $false))
Get-Content config.local.toml

# 3. Replace the "Hey Claude Web" task (answer y), then the power check
#    (answer y to stay awake when plugged in). Add -NoWake to install only
#    the phone web app, without the always-listening wake word.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_service.ps1

# 4. Check: both tasks Running, the web app answers with its version
Get-ScheduledTask -TaskPath '\Kwil\' | Format-Table TaskName, State
Start-Sleep -Seconds 40
Invoke-RestMethod http://127.0.0.1:8765/healthz        # ok: True, version: <sha>
Get-Content logs\host-web.log -Tail 5
.\.venv\Scripts\python.exe -m kwil --update       # up-to-date, or why it skipped
```

The phone keeps working throughout, apart from a few seconds while the web
app restarts on the same `127.0.0.1:8765` (`tailscale serve` is untouched).
To undo: `.\scripts\install_windows_service.ps1 -Uninstall`, then
re-create the old task by hand.

## Health checks and safe updates

So a broken app is found and fixed before you notice it, a watchdog checks
every web app on the machine (yours and each family copy) and new code only
goes live after it has passed a real conversation.

**Install (once).** On the PC, in PowerShell in the project folder:

```powershell
.\scripts\install_watch_task.ps1             # \Kwil\Watch: every 5 minutes, hidden
.\scripts\install_family_tasks.ps1 -Watch    # or together with the family copies' tasks
```

On the Pi: `./scripts/install_watch.sh` (a systemd user timer,
`kwil-watch.timer`). `python -m kwil.watch --status` prints the
last results; `python -m kwil.watch --functional` runs everything now.

**What it checks.** Every 5 minutes, for each instance (`config.toml` with
`.env`, and every `config.<name>.toml` with its `.env.<name>`, the same rule
as `install_family_tasks.ps1`; one whose env file has no `WEB_TOKEN` has no
web app yet, e.g. a wake-only Pi or a family copy that never started, and
is left out; if something answers on its port anyway, its token is set only
in the environment, and `--status` and your banner show it as "running, but
WEB_TOKEN isn't in its env file, so it can't be checked", with one alert:
put `WEB_TOKEN` in its env file): `/healthz` on its port, the page and every
script and stylesheet it loads, that it runs the checked-out version, and
the address the phone opens (`[webapp] public_origin`, else the `tailscale
serve` entry for its port; set `[watch] public_url` to override, `""` to
skip). With `[watch] cloud_url` set, the cloud copy's `/healthz` is checked
too, alerted on like the others but never restarted or rolled back from
here (README "Uptime, error tracking and backups"). After every restart or new version, every `functional_hours` (6) and,
once failed, every `retry_minutes` (30), it also has a **test conversation**
with it, the way people use it, in one session: a quick question ("What is
two plus two?", short and with no change words, so with the router on it is
the turn Haiku answers), a text turn that makes it use a read-only tool (the calendar, else the assignments list, else web
search), a follow-up that depends on the first turn, a spoken turn
(`tests/fixtures/hey_claude_calendar_tomorrow.wav`), a hands-free voice turn
over `/api/voice`, and a last text turn typed while the voice view is still
open. Every turn must answer, and the follow-ups must still remember a
number from the first. The conversation runs in a **check session**: no
history, no memory summary, and only read-only tools are offered or run, so
a check never writes to your calendar, Notion, Gmail, memory or history.
Each one costs a few short Claude requests; `functional_hours` sets how
often (estimated, not measured: roughly 30,000 input tokens per instance
per check).

**What it does about a problem.** An app that doesn't answer first gets
`start_grace_seconds` (300) in case it is starting (a restart, a reboot,
loading its models); nothing is done while an update or deploy holds the
update lock, or while someone's turn is running. Then it is restarted once
(its Task Scheduler task; on Linux `[watch] restart_command`, e.g.
`["systemctl", "--user", "restart", "kwil-web"]`) and given the grace
period again. If that restart ran and it still fails on code newer than the
**last known good** version (the last one whose test conversation passed
here), the checkout goes back to that version (`git reset --keep`, as the
updater does: config, `.env` and `data/` untouched; not when the newer
commits exist only on this machine, nor when HEAD or the branch moved
meanwhile), the apps restart, and that commit is never taken again. Without
a restart that ran (no restart command), it is only reported. Failures that
aren't the code's (no internet, Claude unreachable, rate limits, a rejected
API key) and a phone address (Tailscale) that doesn't answer are reported
but never restarted for or rolled back.

**New code goes live only after the go-live check** (`[watch] gate`, on by
default): each instance is started a second time on a side port (its port
+ `side_port_offset`, 100: 8765 -> 8865, on 127.0.0.1) and must pass the same
test conversation first (a failure, including new code that exits before
it answers, is tried once more before the commit is refused; a busy side
port, after stopping a leftover check copy, a check copy killed by the
system (out of memory on the Pi) or Claude being unreachable says nothing
about the code and is tried again later: for a pull by hand, 1, 5, 15, then
every 60 minutes after the last try (the host supervisor and the watchdog
share that schedule); for an automatic update, at the next `[update]`
check. You are told once if it keeps happening for 3 tries, or for half an
hour on a pull by hand). The automatic updater does this before the running
apps see the new commit; for a pull by hand the host supervisor or the
watchdog does it before restarting anything. A commit that fails is undone
and the old version keeps running. **Update with one command** instead of
`git pull` and restarting:

```powershell
.\scripts\deploy.ps1            # pull, go-live check, switch over (or refuse)
.\scripts\deploy.ps1 -NoPull    # the code checked out now (after a pull by hand)
```

(`./scripts/deploy.sh` on the Pi; `--no-pull`, `--restart`, `--requirements`.)
`install_family_tasks.ps1 -Restart` goes through the same check. Before
anything switches, the installed packages must meet every `>=` minimum in
`requirements.txt`; after a pull by hand that raised one, the gate and
deploy refuse with what's missing until you stop the apps and run
`pip install -r requirements.txt` (or `python -m kwil --update`).
A `>=` line with no environment marker that a machine can't or won't
install (no wheel for it there, say) makes every go-live check on that
machine "can't tell yet", and the alert after 3 tries names the package:
give such a line a marker instead (as `openwakeword>=0.6; platform_system
!= "Linux"` and the Kokoro lines already have), and the check skips it
where the marker says it doesn't apply. An
app in the middle of someone's turn is waited for (up to 2 minutes), then
left running and named, instead of being restarted mid-answer. On a
machine with no web app (a wake-only Pi), there is nothing to start on a
side port: new code must pass the import and `--check` smoke only. A
failed import refuses it; a failed `--check` (often this machine's: an
unplugged mic, no audio device, no API key) is "can't tell yet", tried again
later and reported after 3 tries, never a refusal for good.

**Where you hear about it.** Only when something changes (an app starts
failing, or recovers) and for one-off events (a version refused or rolled
back), never repeatedly:
- your phone app shows a banner ("Mom's app is down since 06:13 (healthz:
  HTTP 502)", or that the checks stopped running) on your own instance only;
  family members never see it;
- the dev team coordinator gets a message through the voice link
  (`kwil/devteam.py`, `[devteam]`, when set up; `[watch] coordinator =
  false` turns it off), so it can find the cause and open a PR;
- `logs/watch.log` has every result, and `data/watch_status.json` the
  current state.

An alert names the app, the check and a fixed reason (an error class or an
HTTP status), never conversation text or a key. When the Claude API itself
is down, the app says "Claude is unreachable right now. I retried a few
times; please try again in a minute." instead of a vague error.

## Preflight: check everything in one pass

```powershell
.\scripts\preflight.ps1                 # Windows (PowerShell 5.1 or 7)
```
```bash
./scripts/preflight.sh --hardware       # Linux / Raspberry Pi
```

Each step prints `PASS`, `FAIL`, `WARN` or `SKIP` (with the reason), then a
table and `GO` (no FAIL; warnings don't block) or `NO-GO`. The full output is saved to
`logs/preflight-<date>-<time>.txt` (gitignored). Values from `.env` are
loaded into the preflight's own process only and are masked as `[REDACTED]`
everywhere, in the console and the report.

| Step | What it checks |
|---|---|
| `git` | on `master`, no uncommitted changes (`config.toml` may differ per machine) |
| `env` | `.env` exists and has `ANTHROPIC_API_KEY` (names only, never values) |
| `check` | `python -m kwil --check` passes |
| `pytest`, `pytest-live` | the full suite, then the live read-only Pinecone/Notion/Google tests, which must not skip |
| `wake-e2e` | `--mode wake --audio-file tests/fixtures/hey_claude_calendar_tomorrow.wav --timings`: a wake, the transcript and a real reply |
| `text-tools` | text mode answers a calendar and a Notion question, and the log shows both tools were called |
| `web` | starts `--mode web` on 127.0.0.1, then the web checks below (including a voice-mode WebSocket turn) |
| `tts-pyttsx3`, `tts-piper` | renders a sentence to a .wav with each engine |
| `audio-devices`, `mic-level` | with `--hardware` only: lists devices, records 3 s and reports the level |
| `power` | Windows only: `WARN` if the PC sleeps or hibernates on AC power (that stops the assistant) or USB selective suspend is on |

`--skip pytest,web` skips steps; `--help` lists the options.

**Web smoke check** (after a deploy, or against the Pi):

```powershell
.\scripts\cloud_smoke.ps1 https://<your-service>.onrender.com
```
```bash
./scripts/cloud_smoke.sh https://<your-service>.onrender.com
```

It checks `/healthz`, that a protected route refuses a request without a
session, logs in with the token in the `WEB_TOKEN` environment variable (or
`WEB_TOKEN` from `.env`; `--token-env` picks another variable), then runs
one text turn, one audio turn and, where the server has voice conversation
mode, one voice turn over the `/api/voice` WebSocket. It never prints the
token. It sends the token only over `https://` (or plain http to this
machine); `--allow-http` overrides that on a network you trust.

**Moving to a Raspberry Pi:** follow [PI_MIGRATION.md](PI_MIGRATION.md).
CI's **pi-arm64** job runs `install_pi.sh`, the tests and the preflight on
Debian Bookworm arm64 with Python 3.11, i.e. a simulated Pi.

## Project layout

```
kwil/
  cli.py        modes, main loop, error handling   (python -m kwil)
  config.py     config.toml + .env loading, logging
  doctor.py     setup check                       (python -m kwil --check)
  chat.py       Claude conversation, tool loop, memory hooks
  router.py     per-turn model choice: Haiku for simple turns, else the main model
  usage.py      API token counts per call, monthly totals and cost estimate
  tts.py        TTS interface, engine choice (auto), pyttsx3, Piper, espeak-ng, print
  tts_kokoro.py Kokoro voice + checksummed download (--download-voice kokoro)
  tts_elevenlabs.py  optional ElevenLabs cloud voice
  voice_picker.py  each person's reply voice in the phone app (/api/voices)
  earcons.py    soft listening / thinking / ended sounds
  stt.py        push-to-talk recording, faster-whisper (beam search, vocabulary prompt)
  stt_cloud.py  optional OpenAI / Deepgram speech-to-text, local Whisper fallback
  wakeword.py   openWakeWord, energy VAD, mic/file sources
  memory_store.py  your instructions and saved memories (SQLite, one file per person)
  memory_web.py Settings -> Memory routes (and the Pinecone notes list)
  projects.py   projects (one per class): prompt, file budget, uploads, suggestions
  projects_web.py  Settings -> Projects routes (/api/projects)
  memory.py     Pinecone recall + conversation summaries ([memory] backend = "pinecone")
  history.py    conversation history: SQLite store, search, resume
  history_cli.py  python -m kwil --history ...
  audio.py      wav I/O, resampling, chimes
  timing.py     per-turn latency               (--timings)
  webapp.py     private phone web app (FastAPI)  (--mode web)
  history_web.py  phone app history API (/api/conversations)
  cloud.py      the web app on a public server (auth wall, lockout, /healthz)
  voice_ws.py   voice conversation WebSocket (/api/voice)
  voice_session.py  voice turns: streamed reply, sentence TTS, barge-in
  sentences.py  sentence splitter for streamed speech
  web/          the page: index.html, app.js, app.css, sw.js, attachments.js; voice.js,
                voice-core.js, voice-worklet.js, voice.css; vad/ (vendored);
                earcons/ (generated by earcons.py)
  preflight.py  one-pass readiness check        (scripts/preflight.ps1 / .sh)
  smoke.py      web app smoke check             (scripts/cloud_smoke.ps1 / .sh)
  host.py       home host supervisor: restarts, updates (python -m kwil.host)
  updater.py    safe update from the stable branch  (--update)
  watch.py      health checks, test conversation, go-live gate (python -m kwil.watch)
  deploy.py     pull, go-live check, switch or refuse (scripts/deploy.ps1 / .sh)
  version.py    git short SHA for /healthz
  webfeatures.py lecture recorder + tutor routes for the phone app
  lecture.py    chunked uploads, transcription job, Notion lecture notes
  tutor.py      homework photos: checks, EXIF removal, Socratic vision call
  attachments.py  files attached with the chat's "+": type sniffing, limits
  mathspeech.py LaTeX read aloud
  web/          the page: index.html, app.js, app.css, sw.js;
                static/ features.js/css, vendored KaTeX
  tools/        calendar.py, gmail.py, google_auth.py (shared sign-in),
                notion.py, notes.py, web.py, history.py, spotify.py, deadlines.py,
                reminders.py, research.py, tool registry
  spotify_web.py  the phone app's "Connect Spotify" (owner only)
  deadlines.py  each person's school deadlines (SQLite): sync upserts, conflicts, deletes
  canvas.py     Canvas calendar feed, REST API, school lookup, Claude date extraction
  student_web.py  the phone app's "School deadlines" card: connect Canvas, sync, list
  push.py       phone notifications: VAPID key, sealed subscriptions, Web Push
  proactive.py  study briefings: deadline rules, scheduler thread, /api/student/brief
  reminders.py  reminders and scheduled tasks: store, RRULE subset, scheduler, routes
  research.py   deep research: job files, the research session, the report, routes
scripts/        install_pi.sh, install_service.sh  (Raspberry Pi)
                install_windows_service.ps1               (PC as the always-on host)
                preflight.ps1/.sh, cloud_smoke.ps1/.sh; ci/ (the arm64 Pi simulation)
                measure_memory.py, container_smoke.sh, ws_smoke.py (cloud image)
                install_watch_task.ps1, install_watch.sh, restart_instance.ps1,
                deploy.ps1/.sh                            (health checks, safe updates)
Dockerfile      the cloud image; render.yaml deploys it on Render
deploy/         kwil.service                       (systemd user unit)
                kwil-watch.service/.timer          (the watchdog every 5 minutes)
assistant/      the package's name before the Kwil rebrand: an alias of kwil/
                (python -m assistant, import assistant.X), kept for one release
tests/          pytest suite (offline; live tests skip without keys)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows),
                              the Docker image, the Pinecone MCP connector,
                              api-contract (real-API conversation, with the secret)

docs/WATCHDOG.md  the dev-team dashboard's hourly watchdog prompt (the dashboard
                  page itself is in the private kylechen839-max/agent-dashboard repo)
tools/session-watch/  Session watcher: a local notice when one of your Claude Code
                  sessions on this PC or Mac waits on you (approval prompt), with an
                  optional Codex summary; it only reports (see its README.md)
tests/          pytest suite (offline; live tests skip without keys);
                tests/js/ node:test unit tests (run by pytest if node is installed)
.github/workflows/tests.yml   CI: pytest on every push + weekly (Ubuntu, Windows)
.github/workflows/pi-arm64.yml  CI: a simulated Pi (Debian Bookworm arm64, Python 3.11)
```

See `PROGRESS.md` for what each stage does and how it was tested,
`BLOCKERS.md` for what is still open for you, `MANUAL_TEST.md` for the final
hands-on checklist, and [BACKLOG.md](BACKLOG.md) for planned work (such as
the hybrid model, before a public release).
