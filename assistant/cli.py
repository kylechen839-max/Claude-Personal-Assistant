"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse
import logging
import os
import re
import threading
import time
from pathlib import Path

import anthropic

from assistant import audio, i18n, usage
from assistant.chat import ClaudeChat
from assistant.timing import TurnTimer
from assistant.stt import Transcriber, record_push_to_talk
from assistant.tools import ToolRegistry
from assistant.tts import ENGINES, make_tts

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct",
             "affirmative", "confirm", "confirmed", "absolutely", "definitely"}
NO_WORDS = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "wait"}
# Chinese (Mandarin) answers. A single character means too much on its own
# (还是明天吧, 好像错了, 是谁, 对了还有一个), so a yes is the whole answer, from
# this short list: each part between punctuation, after leading fillers
# (嗯/哦/啊/呃), must be one of these ("嗯，好的", "好，删吧"). Anything else,
# including a lone 嗯, a question or 不好, is a no.
ZH_EXIT_WORDS = {"再见", "拜拜", "再会"}
ZH_YES_ANSWERS = frozenset({
    "是", "是的", "对", "对的", "好", "好的", "好啊", "好吧", "行", "可以", "确定",
    "确认", "没问题", "删吧", "删除吧", "加吧"})
ZH_FILLERS = "嗯哦啊呃"
CJK = re.compile(r"[\u4e00-\u9fff]")
log = logging.getLogger(__name__)


# "Hey Claude, stop" over a reply: just stop talking (Claude isn't asked).
STOP_PHRASES = {"stop", "stop it", "stop talking", "ok stop", "okay stop", "cancel",
                "never mind", "nevermind", "be quiet", "quiet", "shush", "shh",
                "enough", "that's enough", "thats enough", "thank you", "thanks",
                "停", "停下", "停止", "别说了", "不用说了", "算了", "安静", "好了", "谢谢"}


def is_exit(text: str) -> bool:
    word = text.lower().strip(" .!?,。！？，、~～")
    return word in EXIT_WORDS or word in ZH_EXIT_WORDS


def is_stop(text: str) -> bool:
    words = re.sub(r"[^\w\s']+", " ", text.lower()).split()
    return " ".join(words) in STOP_PHRASES


def zh_is_yes(text: str) -> bool:
    """Whole-answer Chinese yes: every part is on ZH_YES_ANSWERS."""
    if "?" in text or "？" in text:  # "确定？" asks back; it isn't an answer
        return False
    parts = [p.lstrip(ZH_FILLERS) for p in re.split(r"[\s\W_]+", text)]
    parts = [p for p in parts if p]
    return bool(parts) and all(p in ZH_YES_ANSWERS for p in parts)


def download_voice_command(name: str, output_fn=print) -> int:
    """--download-voice: Kokoro (checksummed) or a Piper voice. Setup only:
    voices are never downloaded while answering."""
    from assistant import tts, tts_kokoro

    try:
        if name.lower().startswith("kokoro"):
            variant = name.lower().partition("-")[2] or "int8"
            model, voices = tts_kokoro.download(variant=variant, output_fn=output_fn)
            output_fn(f"Saved {model} and {voices} (checksums ok). With "
                      '[tts] engine = "auto" (the default) Kokoro is used now.')
            if variant != "int8":
                output_fn(f'Set [tts] kokoro_model = "{model.as_posix()}" to use it.')
        else:
            model = tts.download_voice(name)
            output_fn(f"Saved {model}. To use it, set in config.toml:\n"
                      f'[tts]\nengine = "piper"\npiper_voice = "{model.as_posix()}"')
    except Exception as e:
        log.exception("Voice download failed")
        output_fn(f"Download failed: {e}")
        return 1
    return 0


def is_yes(text: str) -> bool:
    """Strict yes: a yes word and no negation ('yes, but no' -> False)."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    phrase = " ".join(re.findall(r"[a-z']+", text.lower()))
    yes = bool(words & YES_WORDS) or phrase in {"go ahead", "do it", "please do"}
    no = bool(words & NO_WORDS)
    if CJK.search(text):  # "好的" yes; "不好", "好不好", "可以吗？", "是谁" no
        return zh_is_yes(text)
    return yes and not no


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant",
                                     description="Hey Claude voice assistant")
    parser.add_argument("--mode", choices=["text", "speak", "voice", "wake", "web"],
                        help="text: type/read; speak: type/listen; voice: "
                             "push-to-talk; wake: always listening (default); "
                             "web: private phone web app (the only mode, and "
                             "the default, with ASSISTANT_PROFILE=server)")
    parser.add_argument("--audio-file",
                        help="wake mode: feed this .wav through the wake-word "
                             "pipeline instead of the live mic, then exit")
    parser.add_argument("--tts", choices=list(ENGINES),
                        help="override the TTS engine from config.toml")
    parser.add_argument("--download-voice", metavar="VOICE",
                        help='download a voice at setup time and exit: "kokoro" '
                             '(int8 model and voices, checksum-verified, into '
                             'models/kokoro/), "kokoro-fp16", or a Piper voice '
                             'such as en_US-lessac-medium (into voices/)')
    parser.add_argument("--config", default="config.toml",
                        help="settings file (default: config.toml)")
    parser.add_argument("--env-file", default=".env",
                        help="secrets file (default: .env); e.g. .env.mom for "
                             "a second, separate instance")
    parser.add_argument("--keyword-path",
                        help="openWakeWord .onnx model for the 'Hey Claude' wake word")
    parser.add_argument("--timings", action="store_true",
                        help="wake and web voice mode: print each turn's latency "
                             "summary to the console too (always in the log file)")
    parser.add_argument("--host",
                        help="web mode: address to listen on (default "
                             "127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int,
                        help="web mode: port (default 8765)")
    parser.add_argument("--show-token", action="store_true",
                        help="web mode: print the access token to the console "
                             "(never written to the log)")
    parser.add_argument("--check", action="store_true",
                        help="check keys, files, audio devices and models, "
                             "then exit (prints no secret values)")
    parser.add_argument("--role", choices=["pi", "pc", "cloud"],
                        help="which backend this is in the Pi -> PC -> cloud "
                             "failover (see FAILOVER.md); overrides "
                             "[failover] role")
    parser.add_argument("--update", action="store_true",
                        help="update now from the tested `stable` branch "
                             "(fast-forward only; rolled back if checks fail)")
    parser.add_argument("--candidate", action="store_true",
                        help="web mode: a side-port copy for the go-live check "
                             "(assistant/watch.py): no history, no saved "
                             "sign-ins, no background jobs, its own log")
    hist = parser.add_argument_group("conversation history")
    hist.add_argument("--history", nargs="+", metavar="ACTION",
                      help="list | search WORDS | show ID | export ID | delete ID")
    hist.add_argument("--format", choices=["md", "json"], default="md",
                      help="--history export format (default md)")
    hist.add_argument("--source", choices=["wake", "text", "web", "voice"],
                      help="--history list/search: only this source")
    hist.add_argument("--since", metavar="YYYY-MM-DD",
                      help="--history list/search: from this day")
    hist.add_argument("--until", metavar="YYYY-MM-DD",
                      help="--history list/search: up to this day")
    hist.add_argument("--pinned", action="store_true",
                      help="--history list/search: pinned only")
    hist.add_argument("--yes", action="store_true",
                      help="--history delete: don't ask first")
    hist.add_argument("--continue", dest="continue_id", metavar="ID",
                      help="text/speak mode: continue a saved conversation "
                           "(an id from --history list, or 'last')")
    hist.add_argument("--incognito", action="store_true",
                      help="save nothing: no history, no memory summary")
    return parser.parse_args(argv)


def get_reply(chat: ClaudeChat, user_text: str, output_fn=print,
              **ask_options) -> str:
    """Ask Claude; on failure return a short sentence that can be spoken.
    `ask_options` (on_text, cancel) go to ClaudeChat.ask."""
    try:
        return chat.ask(user_text, **ask_options)
    except Exception as e:
        return friendly_error(e, user_text, output_fn)


# The API itself is down or overloaded (500, 503, 529), not this computer's
# network: the client has already retried ([assistant] api_max_retries).
CLAUDE_DOWN = tuple(c for c in (getattr(anthropic, n, None) for n in (
    "InternalServerError", "ServiceUnavailableError", "OverloadedError"))
    if isinstance(c, type))


def friendly_error(e: Exception, user_text: str = "", output_fn=print) -> str:
    """Log a failed request and return a short sentence to speak instead."""
    if isinstance(e, anthropic.APIConnectionError):  # includes timeouts
        log.warning("Connection error: %s", e)
        output_fn(f"Connection error: {e}")
        return i18n.t("no_internet")
    if isinstance(e, anthropic.AuthenticationError):
        log.error("Authentication error: %s", e)
        output_fn(f"API error: {e}")
        return i18n.t("bad_key")
    if isinstance(e, anthropic.RateLimitError):
        log.warning("Rate limited: %s", e)
        output_fn(f"API error: {e}")
        return i18n.t("rate_limited")
    if isinstance(e, CLAUDE_DOWN):  # 5xx / overloaded, after the retries
        log.warning("Claude unavailable (HTTP %s): %s",
                    getattr(e, "status_code", "?"), type(e).__name__)
        output_fn(f"API error: {e}")
        return i18n.t("claude_down")
    if isinstance(e, anthropic.APIError):
        log.error("API error: %s", e)
        output_fn(f"API error: {e}")
        return i18n.t("api_error")
    # anything else: keep the session alive
    log.error("Unexpected error answering a %d-character request",
              len(user_text), exc_info=e)
    output_fn(f"Error: {e}")
    return i18n.t("error")


def run_text(chat: ClaudeChat, input_fn=None, output_fn=print,
             tts=None) -> None:
    """Typed input; replies printed, and spoken too if a TTS engine is given."""
    input_fn = input_fn or input
    mode = "speak" if tts else "text"
    output_fn(f"Claude assistant ({mode} mode). Type 'quit' to exit.")
    while True:
        try:
            user_text = input_fn("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_text:
            continue
        if is_exit(user_text):
            break
        reply = get_reply(chat, user_text, output_fn)
        output_fn(f"Claude: {reply}")
        if tts:
            tts.speak(reply)
    finish(chat, output_fn)


def run_voice(chat: ClaudeChat, tts, transcriber: Transcriber,
              record_fn=record_push_to_talk, output_fn=print) -> None:
    """Push-to-talk: Enter to start/stop recording, reply spoken aloud."""
    output_fn("Claude assistant (voice mode). Say 'goodbye' to exit.")
    while True:
        try:
            recording = record_fn()
        except (KeyboardInterrupt, EOFError):
            break
        if recording is None:
            continue
        user_text = transcriber.transcribe(recording)
        output_fn(f"You: {user_text}")
        if not user_text:
            continue
        if is_exit(user_text):
            break
        reply = get_reply(chat, user_text, output_fn)
        output_fn(f"Claude: {reply}")
        tts.speak(reply)
    finish(chat, output_fn)


def finish(chat: ClaudeChat, output_fn=print) -> None:
    end_conversation(chat, output_fn)
    if chat.recorder:
        chat.recorder.flush()  # let the last title/summary finish saving
    output_fn("Bye!")


def run_wake(chat: ClaudeChat, tts, transcriber, listener, source,
             output_fn=print, wake_phrase: str = "Hey Claude",
             conversation_timeout: float = 300, clock=time.monotonic,
             max_failures: int = 5, retry_delay: float = 2.0,
             show_timings: bool = False, timing_clock=time.monotonic,
             follow_up_seconds: float = 0, on_idle=None) -> None:
    """Always listening. Ctrl+C quits. A file source stops at its end.

    With `follow_up_seconds` > 0, after each reply it keeps listening that
    long without the wake word; speech in that window is the next turn of
    the same conversation, and silence ends the conversation (summarized to
    memory, history reset) until the next wake word. With 0, a conversation
    ends on 'goodbye' or after `conversation_timeout` seconds without a
    request. Each turn's latency is logged (`timing_clock` must be the clock
    the listener stamps `wake_time` with); `show_timings` prints it too.
    `on_idle` runs each time it goes back to waiting for the wake word
    (resuming music paused on the wake word)."""
    output_fn(f"Listening for '{wake_phrase}'... (Ctrl+C to quit)")
    last_exchange = clock()
    failures = 0
    timings_out = output_fn if show_timings else None
    try:
        while True:
            try:
                woke, recording = listener.listen(source)
                failures = 0
            except KeyboardInterrupt:
                raise
            except Exception as e:  # e.g. mic unplugged: retry a few times
                failures += 1
                log.exception("Listening failed (%d in a row)", failures)
                if failures >= max_failures:
                    raise
                output_fn(f"(Microphone problem: {e}; retrying)")
                time.sleep(retry_delay)
                continue
            if not woke:
                break  # audio file finished
            stale = take_interruption(tts)  # never carried into a new wake
            if stale is not None:
                log.info("Dropped a request said over the end of the last "
                         "conversation (%s)", stale.by)
            timer = TurnTimer(timing_clock, "wake",
                              at=getattr(listener, "wake_time", None))
            timer.mark("speech_end")
            if chat.history and clock() - last_exchange > conversation_timeout:
                end_conversation(chat, output_fn)  # new conversation starts now
            last_exchange = clock()
            if recording is None:
                output_fn("(heard the wake word but no command)")
                idle(on_idle)
                continue
            keep_listening = run_turn(chat, tts, transcriber, recording,
                                      output_fn, timer, wake_phrase, timings_out)
            while keep_listening:
                cut = take_interruption(tts)
                if cut is not None:  # talked over the reply: his new request
                    timer = TurnTimer(timing_clock, "barge-in")
                    recording = cut.recording
                    if recording is None:
                        output_fn("(heard the wake word but no command)"
                                  if cut.by == "wake word" else
                                  "(stopped talking, but heard no request)")
                        continue  # the follow-up window, if it's on
                elif follow_up_seconds > 0:
                    timer = TurnTimer(timing_clock, "follow-up window")
                    recording = listen_follow_up(listener, source, follow_up_seconds)
                    if recording is None:
                        log.info("No follow-up within %s s", follow_up_seconds)
                        break
                else:
                    break
                timer.mark("speech_end")
                last_exchange = clock()
                keep_listening = run_turn(chat, tts, transcriber, recording,
                                          output_fn, timer, wake_phrase,
                                          timings_out, barge_in=cut is not None)
            # no barge-in to answer and no follow-up: back to the wake word
            if follow_up_seconds > 0:
                end_conversation(chat, output_fn)
            idle(on_idle)
    except KeyboardInterrupt:
        pass
    idle(on_idle)  # quitting mid-conversation: music paused for it plays again
    finish(chat, output_fn)


def interruption_of(tts):
    """How Kyle talked over the last thing `tts` said (a
    wakeword.Interruption from BargeInSpeaker), or None."""
    from assistant.wakeword import Interruption

    cut = getattr(tts, "interruption", None)
    return cut if isinstance(cut, Interruption) else None


def take_interruption(tts):
    """interruption_of(tts), cleared so it's handled once."""
    cut = interruption_of(tts)
    if cut is not None:
        tts.interruption = None
    return cut


def idle(on_idle) -> None:
    """run_wake's on_idle hook; a failure there never stops listening."""
    if on_idle is None:
        return
    try:
        on_idle()
    except Exception:
        log.exception("Back-to-idle hook failed")


def listen_follow_up(listener, source, seconds: float):
    """The follow-up recording, or None (silence, or the mic failed)."""
    try:
        return listener.record_follow_up(source, seconds)
    except KeyboardInterrupt:
        raise
    except Exception:
        log.exception("Listening for a follow-up failed")
        return None


def run_turn(chat: ClaudeChat, tts, transcriber, recording, output_fn,
             timer: TurnTimer, wake_phrase: str, timings_out=None,
             barge_in: bool = False) -> bool:
    """One request and its spoken reply. True if a follow-up may come."""
    try:
        return handle_command(chat, tts, transcriber, recording, output_fn,
                              timer, wake_phrase, barge_in=barge_in)
    except KeyboardInterrupt:
        raise
    except Exception:  # never let one bad request stop the assistant
        log.exception("Error handling a command")
        output_fn("(Something went wrong; still listening)")
        try:
            tts.speak(i18n.t("error"))
        except Exception:
            log.exception("TTS failed too")
        return True
    finally:
        report_timings(timer, timings_out)


def end_conversation(chat: ClaudeChat, output_fn=print) -> None:
    summary = chat.end_conversation()
    if summary:
        output_fn(f"(Saved to memory: {summary})")


def strip_wake_phrase(text: str, wake_phrase: str | None) -> str:
    """'Hey Claude, what time is it?' -> 'what time is it?' (people often
    repeat the wake word during the follow-up window)."""
    if not wake_phrase:
        return text
    words = r"\W+".join(re.escape(w) for w in wake_phrase.split())
    return re.sub(rf"^\W*{words}\b\W*", "", text, flags=re.IGNORECASE)


def loggable_reply(chat, reply: str) -> str:
    """The reply for the log file, or a placeholder when it may quote
    private content (an email, a personal note)."""
    if getattr(chat, "private_turn", False) is True:
        return f"<{len(reply)} characters; private tool content not logged>"
    return reply


def report_timings(timer: TurnTimer, output_fn=None) -> None:
    """One summary line per turn in the log, and on the console if asked."""
    line = timer.summary()
    log.info(line)
    if output_fn:
        output_fn(f"({line})")


def speak(tts, text: str, timer: TurnTimer | None = None) -> None:
    """Speak, marking the moment sound starts. Engines that report it
    (on_audio_start) are exact; for others the call time is used."""
    if timer is None:
        tts.speak(text)
        return
    if not hasattr(tts, "on_audio_start"):
        timer.mark("audio")
        tts.speak(text)
        return
    tts.on_audio_start = lambda: timer.mark("audio")
    try:
        tts.speak(text)
    finally:
        tts.on_audio_start = None


def handle_command(chat: ClaudeChat, tts, transcriber, recording,
                   output_fn=print, timer: TurnTimer | None = None,
                   wake_phrase: str | None = None, barge_in: bool = False) -> bool:
    """Transcribe, answer, speak. Returns True if the conversation goes on
    (a follow-up may come), False if nothing was heard or it was 'goodbye'.
    `barge_in`: the request interrupted a reply; "stop" then just stops."""
    heard = transcriber.transcribe(recording)
    if timer:
        timer.mark("transcript")
    output_fn(f"You: {heard}")
    log.info("Heard %d characters", len(heard))
    log.debug("Heard: %r", heard)
    user_text = strip_wake_phrase(heard, wake_phrase).strip()
    if not user_text:
        return bool(heard)  # just "Hey Claude": still listening
    if is_exit(user_text):
        tts.speak(i18n.t("goodbye"))
        if take_interruption(tts) is not None:  # said over "Bye!": dropped
            log.info("Dropped a request said over the goodbye")
        end_conversation(chat, output_fn)
        return False
    if barge_in and is_stop(user_text):
        log.info("Stopped talking (barge-in)")
        return True
    reply = get_reply(chat, user_text, output_fn)
    if timer:
        timer.mark("reply")
    output_fn(f"Claude: {reply}")
    log.info("Replied with %d characters", len(reply or ""))
    log.debug("Replied: %r", loggable_reply(chat, reply))
    speak(tts, reply, timer)
    cut = interruption_of(tts)
    if cut is not None:  # talked over: history keeps (about) what was said
        if chat.mark_interrupted(cut.spoken):
            log.info("Kept about %d characters of the interrupted reply",
                     len(cut.spoken))
    return True


def show_voice_timings(output_fn=print) -> logging.Handler:
    """--timings in web mode: print each voice-conversation turn's latency
    line (it is always in the log file)."""
    class Timings(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if message.startswith("Turn timings"):
                output_fn(f"({message})")

    handler = Timings(logging.INFO)
    voice_log = logging.getLogger("assistant.voice_session")
    voice_log.addHandler(handler)
    if voice_log.getEffectiveLevel() > logging.INFO:
        voice_log.setLevel(logging.INFO)
    return handler


def build_tools(config: dict, output_fn=print) -> ToolRegistry:
    """Register every service that is enabled and has credentials."""
    registry = ToolRegistry()
    tz = config["assistant"]["timezone"] or None
    register_google(registry, config, output_fn)
    if config["notion"]["enabled"] and os.environ.get("NOTION_TOKEN"):
        from assistant.tools.notion import NotionAssignments

        registry.register(*NotionAssignments(config["notion"]["database_id"],
                                             timezone=tz).tools())
    elif config["notion"]["enabled"]:
        output_fn("(Notion tools off: NOTION_TOKEN not set)")
    register_devteam(registry, config, output_fn)
    register_home_assistant(registry, config, output_fn)
    register_teamchat(registry, config, output_fn)
    register_spotify(registry, config, output_fn)
    web = config["web"]
    if web["enabled"]:
        from assistant.tools import web as web_tools

        registry.add_server_tools(
            *web_tools.server_tools(config["assistant"]["model"], web))
        if web["browser"]:
            registry.register(*web_tools.browser_tools())
    return registry


def register_google(registry: ToolRegistry, config: dict, output_fn=print) -> None:
    """Calendar and Gmail tools. With [webapp] google_connect (the phone's
    "connect Google" button) only once that sign-in saved a token: without
    one, Calendar would start a browser sign-in on this computer."""
    cal = config["calendar"]
    if not cal["enabled"]:
        return
    from assistant.tools.calendar import GoogleCalendar, has_credentials
    from assistant.tools.google_auth import load_token_info

    if config["webapp"]["google_connect"]:
        try:
            ready = load_token_info(cal["token"]) is not None
        except (OSError, ValueError):
            ready = False
        if not ready:
            output_fn("(Google tools off until the phone app's "
                      "\"connect Google\" sign-in finishes)")
            return
    elif not has_credentials(cal["credentials"], cal["token"]):
        output_fn(f"(Calendar tools off: {cal['credentials']} not found and "
                  "GOOGLE_TOKEN_JSON not set)")
        register_gmail(registry, config, output_fn)
        return
    registry.register(*GoogleCalendar(
        credentials_path=cal["credentials"], token_path=cal["token"],
        calendar_id=cal["calendar_id"], timezone=config["assistant"]["timezone"] or None,
        save_token=cal["save_token"]).tools())
    register_gmail(registry, config, output_fn)


def register_spotify(registry: ToolRegistry, config: dict, output_fn=print,
                     http=None) -> bool:
    """Spotify playback tools, once [spotify] is on and signed in
    (python -m assistant.tools.spotify, or the phone's "Connect Spotify")."""
    if not config["spotify"]["enabled"]:
        return False
    from assistant.tools.spotify import (CLIENT_ID_ENV, SIGN_IN_COMMAND,
                                         player_from_config)

    player = player_from_config(config, http=http)
    if player is None:
        output_fn(f"(Spotify tools off: set {CLIENT_ID_ENV} and sign in once "
                  f"with {SIGN_IN_COMMAND})")
        return False
    registry.register(*player.tools())
    return True


def register_devteam(registry: ToolRegistry, config: dict, output_fn=print) -> bool:
    """message_dev_team / check_dev_team, when a coordinator session is set.
    Replies are read from Notion, so they also need NOTION_TOKEN."""
    team = config["devteam"]
    if not team["enabled"]:
        return False
    from assistant.devteam import link_from_config, session_id

    if not session_id(team["coordinator_session"]):
        output_fn("(Dev team tools off: [devteam] coordinator_session is not "
                  "a session id or claude.ai/code link)")
        return False
    if not (os.environ.get("NOTION_TOKEN") and config["notion"]["database_id"]):
        output_fn("(Dev team replies off: NOTION_TOKEN not set; messages still go out)")
    link = link_from_config(config, team["state_file"])
    registry.register(*link.tools())
    return True


def register_home_assistant(registry: ToolRegistry, config: dict,
                            output_fn=print) -> bool:
    """ha_list / ha_state / ha_control, when [home_assistant] is on and
    HA_URL (or [home_assistant] url) and HA_TOKEN are set. The owner's
    registry only: family members never get it (family.py)."""
    ha = config["home_assistant"]
    if not ha["enabled"]:
        return False
    url = ha["url"] or os.environ.get("HA_URL", "")
    if not (url and os.environ.get("HA_TOKEN")):
        output_fn("(Home Assistant tools off: HA_URL or HA_TOKEN not set)")
        return False
    from assistant.tools.home_assistant import HomeAssistant, plain_http_off_lan

    if plain_http_off_lan(url):
        output_fn("(Warning: Home Assistant is at a plain http:// address outside "
                  "your home network, so its token travels unencrypted; use https)")
    registry.register(*HomeAssistant(
        url=url, domains=ha["domains"], scripts=ha["scripts"],
        secure_entities=ha["secure_entities"],
        cache_seconds=float(ha["cache_seconds"]),
        timeout=float(ha["timeout"])).tools())
    return True


def register_teamchat(registry: ToolRegistry, config: dict, output_fn=print,
                      environ=os.environ, session=None) -> bool:
    """chat_* tools (read Slack, Discord, GitHub; file a digest in Notion),
    for each [teamchat] service that is on and has its token in .env. The
    owner's registry only: family members never get them (family.py)."""
    tc = config["teamchat"]
    if not tc["enabled"]:
        return False
    from assistant.tools.teamchat import teamchat_from_config

    notion = None
    if config["notion"]["enabled"] and environ.get("NOTION_TOKEN"):
        from assistant.tools.notion import NotionAssignments

        notion = NotionAssignments(config["notion"]["database_id"],
                                   token=environ["NOTION_TOKEN"],
                                   timezone=config["assistant"]["timezone"] or None)
    chat, missing = teamchat_from_config(config, environ=environ, session=session,
                                         notion=notion)
    for label, var in missing:  # only services that are turned on
        output_fn(f"({label} tools off: {var} not set)")
    if not chat.providers:
        return False
    if notion is None:
        output_fn("(Team chat digests off: they need NOTION_TOKEN)")
    registry.register(*chat.tools())
    return True


def register_gmail(registry: ToolRegistry, config: dict, output_fn=print) -> bool:
    """Gmail tools, if the Google token has the Gmail scopes. A token from
    before Gmail support doesn't: say how to grant them and leave them off."""
    from assistant.tools.google_auth import (GMAIL_SCOPES, SIGN_IN_COMMAND,
                                             load_token_info, missing_scopes)

    cal = config["calendar"]
    if not config["gmail"]["enabled"] or not cal["enabled"]:
        return False
    try:
        if load_token_info(cal["token"]) is None:
            return False  # no Google sign-in at all (Calendar says so)
        lacking = missing_scopes(GMAIL_SCOPES, cal["token"])
    except (OSError, ValueError) as e:
        output_fn(f"(Gmail tools off: Google token unreadable: {e})")
        return False
    if lacking:
        output_fn("(Gmail tools off: Gmail access not granted yet. Run "
                  f"{SIGN_IN_COMMAND} once to sign in again.)")
        return False
    from assistant.tools.gmail import GmailInbox

    registry.register(*GmailInbox(
        credentials_path=cal["credentials"], token_path=cal["token"],
        timezone=config["assistant"]["timezone"] or None,
        max_summary=config["gmail"]["max_summary"]).tools())
    return True


def build_memory(config: dict, output_fn=print):
    mem = config["memory"]
    if not mem["enabled"]:
        return None
    if not os.environ.get("PINECONE_API_KEY"):
        output_fn("(Memory off: PINECONE_API_KEY not set)")
        return None
    from assistant.memory import PineconeMemory

    return PineconeMemory(index_name=mem["index"], namespace=mem["namespace"],
                          search_namespaces=mem["search_namespaces"],
                          embed_model=mem["embed_model"], top_k=mem["top_k"],
                          min_score=mem["min_score"],
                          text_fields=mem["text_fields"])


def add_memory_tools(tools: ToolRegistry, memory) -> None:
    """Let Claude search memory and save notes on request (memory on only)."""
    if memory is not None:
        from assistant.tools.memory_tools import memory_tools

        tools.register(*memory_tools(memory))


def build_memory_review(config: dict, memory):
    """The memory review (memory_review.py) for this memory, or None."""
    if memory is None:
        return None
    from assistant.memory_review import MemoryReview

    mem = config["memory"]
    return MemoryReview(memory, timezone=config["assistant"]["timezone"] or None,
                        weekly_review=mem["weekly_review"],
                        state_file=mem["review_state_file"] or None)


def register_memory_review(registry: ToolRegistry, review) -> None:
    """The read-only review_memory tool ("what did you remember about me
    this week?"); deleting one goes through delete_note (notes on)."""
    if review is not None:
        registry.register(*review.tools())


def register_notes(registry: ToolRegistry, memory, config: dict) -> bool:
    """Quick-capture note tools; they need Pinecone memory. They replace
    the plain `remember` memory tool (same job, plus tags, listing and
    deleting); `search_memory` stays for searching all of memory."""
    if memory is None or not config["notes"]["enabled"]:
        return False
    from assistant.tools.notes import QuickNotes

    registry.tools.pop("remember", None)
    registry.register(*QuickNotes(memory,
                                  timezone=config["assistant"]["timezone"] or None,
                                  min_score=config["notes"]["min_score"],
                                  on_saved=note_to_history).tools())
    return True


def note_to_history(note: dict) -> bool:
    """Mark in the current conversation's history that a note was saved
    (through the history module's public function; nothing if history is
    off). The note's text stays only in Pinecone, so delete_note removes it
    completely."""
    from assistant import history

    tags = f" (tags: {', '.join(note['tags'])})" if note.get("tags") else ""
    return history.record_event(None, "note",
                                f"Saved a note to long-term memory{tags}.")


def text_confirm(input_fn=None):
    def confirm(question: str) -> bool:
        confirm.last_answer = (input_fn or input)(f"{question} (yes/no): ")
        return is_yes(confirm.last_answer)
    return confirm


def voice_confirm(tts, record_fn, transcriber, output_fn=print):
    """Ask out loud, record the answer, and treat only a clear yes as yes."""
    def confirm(question: str) -> bool:
        output_fn(f"Claude: {question}")
        tts.speak(question + i18n.t("say_yes_no"))
        cut = take_interruption(tts)
        if cut is not None:
            # talked over the question: never a yes, and what he said
            # (maybe an early "yes") isn't taken as an answer either
            log.info("Confirmation question interrupted (%s): treated as no", cut.by)
            output_fn("(Interrupted the question, so it's a no)")
            confirm.last_answer = ""
            return False
        recording = record_fn()
        answer = transcriber.transcribe(recording) if recording is not None else ""
        output_fn(f"You: {answer}")
        confirm.last_answer = answer  # saved in conversation history
        return is_yes(answer)
    return confirm


def build_wake_components(args, config: dict, music=None):
    """`music`: a tools.spotify.SpotifyPlayer to pause on the wake word."""
    from assistant.wakeword import (EnergyVAD, FileSource, MicSource,
                                    WakeListener, barge_in_mode, make_detector)

    wake = config["wake"]
    detector = make_detector(keyword_path=args.keyword_path or wake["keyword_path"],
                             builtin_keyword=wake["builtin_keyword"],
                             sensitivity=wake["sensitivity"])
    chime, soft_chime = audio.make_chime(), audio.make_follow_up_chime()
    chimes = wake["chime"] and not args.audio_file
    on_wake = (lambda: audio.play(chime)) if chimes else None
    if music is not None:
        on_wake = chain(music.pause_for_listening, on_wake)
    on_follow_up = ((lambda: audio.play(soft_chime))
                    if chimes and wake["follow_up_chime"] else None)
    listener = WakeListener(detector, vad=EnergyVAD(wake["vad_min_threshold"]),
                            on_wake=on_wake, on_follow_up=on_follow_up,
                            silence_ms=wake["silence_ms"],
                            max_seconds=wake["max_seconds"],
                            no_speech_timeout=wake["no_speech_timeout"])
    device = audio.resolve_mic_device(wake["mic_device"])
    # with barge-in the file is read while replies play: in real time, as a mic
    pace = 1.0 if barge_in_mode(wake["barge_in"]) != "off" else 0
    source = (FileSource(args.audio_file, detector.frame_length, pace=pace)
              if args.audio_file else MicSource(detector.frame_length, device=device))
    return listener, source, detector.phrase


def barge_in_speaker(tts, listener, source, config: dict):
    """`tts`, listening for Kyle talking over its replies ([wake] barge_in)."""
    from assistant.wakeword import BargeInSpeaker, barge_in_mode

    wake = config["wake"]
    mode = barge_in_mode(wake["barge_in"])
    if mode == "off":
        return tts
    return BargeInSpeaker(tts, listener, source, mode=mode,
                          barge_in_ms=wake["barge_in_ms"],
                          echo_ratio=wake["barge_in_echo_ratio"])


def chain(first, then=None):
    """Call `first`, then `then` (if any)."""
    if then is None:
        return first

    def both():
        first()
        then()
    return both


def music_to_pause(config: dict, tools: ToolRegistry):
    """The SpotifyPlayer to pause while listening, if [spotify]
    pause_while_listening is on and Spotify is connected."""
    if not (config["spotify"]["enabled"]
            and config["spotify"]["pause_while_listening"]):
        return None
    from assistant.tools.spotify import player_of

    return player_of(tools)


def run_wake_mode(args, config: dict, chat: ClaudeChat, tts, transcriber,
                  tools: ToolRegistry) -> None:
    """--mode wake: the listener (pausing Spotify on the wake word, if
    connected; never for --audio-file), barge-in ([wake] barge_in; the wake
    word over a reply pauses Spotify too, through the listener's on_wake),
    spoken confirmations, run_wake (resuming Spotify when back to waiting)."""
    music = None if args.audio_file else music_to_pause(config, tools)
    listener, source, phrase = build_wake_components(args, config, music)
    tts = barge_in_speaker(tts, listener, source, config)
    tools.confirm = voice_confirm(
        tts, lambda: listener.record_reply(source), transcriber)
    run_wake(chat, tts, transcriber, listener, source, wake_phrase=phrase,
             conversation_timeout=config["assistant"]["conversation_timeout"],
             show_timings=args.timings,
             follow_up_seconds=config["wake"]["follow_up_seconds"],
             on_idle=music.resume_after_listening if music else None)


def build_chat(config: dict, tools=None, memory=None,
               recorder=None, review=None) -> ClaudeChat:
    a = config["assistant"]
    client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                 timeout=a["api_timeout"])
    chat = ClaudeChat(client=client, model=a["model"], max_tokens=a["max_tokens"],
                      max_history=a["max_history"], tools=tools,
                      max_tool_rounds=a["max_tool_rounds"], memory=memory,
                      recorder=recorder, language=a["language"],
                      fast_model=a["fast_model"], router=a["router"])
    if review is not None:  # [memory] weekly_review (off by default)
        chat.review_nudge = review.nudge
    if recorder is not None and recorder.client_fn is None:
        recorder.client_fn = lambda: chat.client
    return chat


HISTORY_SOURCES = {"text": "text", "speak": "text", "voice": "wake", "wake": "wake"}


def build_recorder(config: dict, store, mode: str, incognito: bool = False):
    """A history Recorder for this mode, or None if history is off."""
    from assistant.history import Recorder

    if store is None:
        return None
    return Recorder(store, HISTORY_SOURCES.get(mode, mode),
                    incognito=incognito or config["history"]["incognito"],
                    title_model=config["history"]["title_model"])


def add_history_tools(registry: ToolRegistry, config: dict, store, memory) -> None:
    if store is None:
        return
    from zoneinfo import ZoneInfo

    from assistant.tools.history import history_tools

    tz = config["assistant"]["timezone"]
    registry.register(*history_tools(store, memory, ZoneInfo(tz) if tz else None))


def continue_conversation(chat: ClaudeChat, conversation_id: str,
                          output_fn=print) -> bool:
    """--continue ID (or 'last'): load a saved conversation into the chat."""
    store = chat.recorder.store if chat.recorder else None
    if store is None:
        output_fn("Conversation history is off; can't continue.")
        return False
    if conversation_id == "last":
        recent = store.list(limit=1)
        conversation_id = recent[0]["id"] if recent else ""
    try:
        conv = chat.resume(conversation_id)
    except KeyError:
        output_fn(f"No conversation with id {conversation_id!r} "
                  "(see: python -m assistant --history list)")
        return False
    output_fn(f"(Continuing '{conv['title'] or 'untitled'}', "
              f"{conv['message_count']} messages)")
    return True


def separate_instance(env_file: str) -> bool:
    """True for a copy run with its own env file (Mom's, Kai's): any path but
    this folder's .env, however it is spelled ("./.env", an absolute path)."""
    return Path(env_file).resolve() != Path(".env").resolve()


def main(argv=None) -> int:
    from assistant.config import inherited_env_conflicts, load_config, setup_logging

    args = parse_args(argv)
    inherited = dict(os.environ)  # before load_dotenv adds the env file
    config = load_config(args.config, env_file=args.env_file)
    separate = separate_instance(args.env_file)
    if separate or config["webapp"]["google_connect"]:
        # a separate instance (e.g. --env-file .env.mom): nothing of the
        # owner's may leak in from the shell that started it
        leaked = inherited_env_conflicts(inherited, args.env_file)
        if leaked:
            message = (f"Refusing to start: {', '.join(leaked)} "
                       f"{'is' if len(leaked) == 1 else 'are'} set in this shell, "
                       f"not by {args.env_file}, and would point this instance at "
                       "someone else's data. Start it from a clean shell (see "
                       "README.md, \"A Chinese instance\").")
            print(message)
            # also in this instance's log: a family task runs hidden
            # (pythonw), where nobody sees stdout
            try:
                setup_logging(config)
                log.error(message)
            except Exception:  # a log that can't be opened: stdout only
                pass
            return 1
    i18n.set_language(config["assistant"]["language"])
    if args.role:
        config["failover"]["role"] = args.role
    if args.candidate:
        from assistant.watch import candidate_config

        candidate_config(config, args.config)
    log_file = setup_logging(config)
    usage.configure(config["assistant"]["usage_file"])  # API token totals
    if separate and config["teamchat"]["enabled"]:
        # a family member's instance: Kyle's team chat tokens may be in the
        # environment it inherits, and the chat tools are his alone
        config["teamchat"]["enabled"] = False
        log.info("Team chat off: a separate instance (--env-file %s)", args.env_file)
    if args.download_voice:
        return download_voice_command(args.download_voice)
    server = config["profile"] == "server"
    if args.check:
        from assistant import doctor

        return doctor.report(doctor.run_checks(config, args.keyword_path))
    if args.update:
        from assistant import updater

        return updater.main()
    if args.history:
        from assistant import history_cli

        return history_cli.run(args, config,
                               memory_fn=lambda: build_memory(config, lambda _: None))
    args.mode = args.mode or ("web" if server else "wake")
    if server and args.mode not in ("web", "text"):
        # no microphone or speakers on a server
        print(f"--mode {args.mode} needs audio devices; with "
              "ASSISTANT_PROFILE=server use --mode web.")
        return 1
    log.info("Starting in %s mode (model %s)", args.mode, config["assistant"]["model"])
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env (see README.md).")
        return 1
    tts_cfg = config["tts"]
    tts_name = args.tts or tts_cfg["engine"]
    stt_cfg = config["stt"]
    try:
        if args.mode == "web" and server:
            from assistant import cloud

            return cloud.serve(config, port=args.port, tts_name=tts_name)
        if args.mode == "web":
            from assistant import webapp

            if args.timings:  # voice conversation turns: one line each
                show_voice_timings()

            return webapp.serve(config, host=args.host, port=args.port,
                                tts_name=tts_name, show_token=args.show_token,
                                env_file=args.env_file)
        from assistant.history import open_store

        tools = build_tools(config)
        memory = build_memory(config)
        add_memory_tools(tools, memory)
        register_notes(tools, memory, config)
        review = build_memory_review(config, memory)
        register_memory_review(tools, review)
        store = open_store(config)
        add_history_tools(tools, config, store, memory)
        recorder = build_recorder(config, store, args.mode, args.incognito)
        chat = build_chat(config, tools, memory, recorder, review)
        if chat.incognito:
            print("(Incognito: this session won't be saved)")
        elif recorder:
            recorder.finish_orphans()  # title anything a crash left behind
        if args.continue_id:
            if args.mode not in ("text", "speak"):
                print("--continue works with --mode text or speak "
                      "(by voice, say: continue our conversation about ...)")
                return 1
            if not continue_conversation(chat, args.continue_id):
                return 1
        if args.mode in ("text", "speak"):
            tools.confirm = text_confirm()
            tts = make_tts(tts_name, **tts_cfg) if args.mode == "speak" else None
            run_text(chat, tts=tts)
            return 0
        tts = make_tts(tts_name, **tts_cfg)
        if callable(getattr(tts, "warm_up", None)):  # load the voice model now
            threading.Thread(target=tts.warm_up, name="tts-warm-up",
                             daemon=True).start()
        transcriber = Transcriber(stt_cfg["model"], stt_cfg["device"],
                                  stt_cfg["compute_type"],
                                  language=config["assistant"]["language"])
        if args.mode == "voice":
            mic = audio.resolve_mic_device(config["wake"]["mic_device"])

            def record():
                return record_push_to_talk(device=mic)
            tools.confirm = voice_confirm(tts, record, transcriber)
            run_voice(chat, tts, transcriber, record_fn=record)
        else:
            run_wake_mode(args, config, chat, tts, transcriber, tools)
    except KeyboardInterrupt:
        print("Bye!")
    except Exception as e:  # startup problems: missing key, no mic, bad config
        log.exception("Fatal error")
        print(f"Error: {e}\n(Details in {log_file or 'the log above'})")
        return 1
    return 0
