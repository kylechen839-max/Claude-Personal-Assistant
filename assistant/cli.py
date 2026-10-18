"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse
import logging
import os
import re
import time

import anthropic

from assistant import audio
from assistant.chat import ClaudeChat
from assistant.timing import TurnTimer
from assistant.stt import Transcriber, record_push_to_talk
from assistant.tools import ToolRegistry
from assistant.tts import make_tts

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct",
             "affirmative", "confirm", "confirmed", "absolutely", "definitely"}
NO_WORDS = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "wait"}
log = logging.getLogger(__name__)


def is_exit(text: str) -> bool:
    return text.lower().strip(" .!?,") in EXIT_WORDS


def is_yes(text: str) -> bool:
    """Strict yes: a yes word and no negation ('yes, but no' -> False)."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    phrase = " ".join(re.findall(r"[a-z']+", text.lower()))
    yes = bool(words & YES_WORDS) or phrase in {"go ahead", "do it", "please do"}
    return yes and not (words & NO_WORDS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant",
                                     description="Hey Claude voice assistant")
    parser.add_argument("--mode", choices=["text", "speak", "voice", "wake", "web"],
                        default="wake",
                        help="text: type/read; speak: type/listen; voice: "
                             "push-to-talk; wake: always listening (default); "
                             "web: private phone web app")
    parser.add_argument("--audio-file",
                        help="wake mode: feed this .wav through the wake-word "
                             "pipeline instead of the live mic, then exit")
    parser.add_argument("--tts", choices=["pyttsx3", "piper", "print"],
                        help="override the TTS engine from config.toml")
    parser.add_argument("--config", default="config.toml",
                        help="settings file (default: config.toml)")
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
    parser.add_argument("--update", action="store_true",
                        help="update now from the tested `stable` branch "
                             "(fast-forward only; rolled back if checks fail)")
    return parser.parse_args(argv)


def get_reply(chat: ClaudeChat, user_text: str, output_fn=print,
              **ask_options) -> str:
    """Ask Claude; on failure return a short sentence that can be spoken.
    `ask_options` (on_text, cancel) go to ClaudeChat.ask."""
    try:
        return chat.ask(user_text, **ask_options)
    except Exception as e:
        return friendly_error(e, user_text, output_fn)


def friendly_error(e: Exception, user_text: str = "", output_fn=print) -> str:
    """Log a failed request and return a short sentence to speak instead."""
    if isinstance(e, anthropic.APIConnectionError):  # includes timeouts
        log.warning("Connection error: %s", e)
        output_fn(f"Connection error: {e}")
        return "Sorry, I can't reach the internet right now."
    if isinstance(e, anthropic.AuthenticationError):
        log.error("Authentication error: %s", e)
        output_fn(f"API error: {e}")
        return "Sorry, my Anthropic API key isn't working. Please check it."
    if isinstance(e, anthropic.RateLimitError):
        log.warning("Rate limited: %s", e)
        output_fn(f"API error: {e}")
        return "I'm getting too many requests right now. Try again in a minute."
    if isinstance(e, anthropic.APIError):
        log.error("API error: %s", e)
        output_fn(f"API error: {e}")
        return "Sorry, something went wrong talking to Claude."
    # anything else: keep the session alive
    log.error("Unexpected error answering %r", user_text, exc_info=e)
    output_fn(f"Error: {e}")
    return "Sorry, something went wrong."


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
    output_fn("Bye!")


def run_wake(chat: ClaudeChat, tts, transcriber, listener, source,
             output_fn=print, wake_phrase: str = "Hey Claude",
             conversation_timeout: float = 300, clock=time.monotonic,
             max_failures: int = 5, retry_delay: float = 2.0,
             show_timings: bool = False, timing_clock=time.monotonic,
             follow_up_seconds: float = 0) -> None:
    """Always listening. Ctrl+C quits. A file source stops at its end.

    With `follow_up_seconds` > 0, after each reply it keeps listening that
    long without the wake word; speech in that window is the next turn of
    the same conversation, and silence ends the conversation (summarized to
    memory, history reset) until the next wake word. With 0, a conversation
    ends on 'goodbye' or after `conversation_timeout` seconds without a
    request. Each turn's latency is logged (`timing_clock` must be the clock
    the listener stamps `wake_time` with); `show_timings` prints it too."""
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
            timer = TurnTimer(timing_clock, "wake",
                              at=getattr(listener, "wake_time", None))
            timer.mark("speech_end")
            if chat.history and clock() - last_exchange > conversation_timeout:
                end_conversation(chat, output_fn)  # new conversation starts now
            last_exchange = clock()
            if recording is None:
                output_fn("(heard the wake word but no command)")
                continue
            keep_listening = run_turn(chat, tts, transcriber, recording,
                                      output_fn, timer, wake_phrase, timings_out)
            if follow_up_seconds <= 0:
                continue
            while keep_listening:
                timer = TurnTimer(timing_clock, "follow-up window")
                recording = listen_follow_up(listener, source, follow_up_seconds)
                if recording is None:
                    log.info("No follow-up within %s s", follow_up_seconds)
                    break
                timer.mark("speech_end")
                keep_listening = run_turn(chat, tts, transcriber, recording,
                                          output_fn, timer, wake_phrase,
                                          timings_out)
            end_conversation(chat, output_fn)  # back to waiting for the wake word
    except KeyboardInterrupt:
        pass
    finish(chat, output_fn)


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
             timer: TurnTimer, wake_phrase: str, timings_out=None) -> bool:
    """One request and its spoken reply. True if a follow-up may come."""
    try:
        return handle_command(chat, tts, transcriber, recording, output_fn,
                              timer, wake_phrase)
    except KeyboardInterrupt:
        raise
    except Exception:  # never let one bad request stop the assistant
        log.exception("Error handling a command")
        output_fn("(Something went wrong; still listening)")
        try:
            tts.speak("Sorry, something went wrong.")
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
                   wake_phrase: str | None = None) -> bool:
    """Transcribe, answer, speak. Returns True if the conversation goes on
    (a follow-up may come), False if nothing was heard or it was 'goodbye'."""
    heard = transcriber.transcribe(recording)
    if timer:
        timer.mark("transcript")
    output_fn(f"You: {heard}")
    log.info("Heard: %r", heard)
    user_text = strip_wake_phrase(heard, wake_phrase).strip()
    if not user_text:
        return bool(heard)  # just "Hey Claude": still listening
    if is_exit(user_text):
        tts.speak("Goodbye!")
        end_conversation(chat, output_fn)
        return False
    reply = get_reply(chat, user_text, output_fn)
    if timer:
        timer.mark("reply")
    output_fn(f"Claude: {reply}")
    log.info("Replied: %r", reply)
    speak(tts, reply, timer)
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
    from pathlib import Path

    registry = ToolRegistry()
    tz = config["assistant"]["timezone"] or None
    cal = config["calendar"]
    if cal["enabled"] and (Path(cal["credentials"]).exists()
                           or Path(cal["token"]).exists()):
        from assistant.tools.calendar import GoogleCalendar

        registry.register(*GoogleCalendar(
            credentials_path=cal["credentials"], token_path=cal["token"],
            calendar_id=cal["calendar_id"], timezone=tz).tools())
    elif cal["enabled"]:
        output_fn(f"(Calendar tools off: {cal['credentials']} not found)")
    if config["notion"]["enabled"] and os.environ.get("NOTION_TOKEN"):
        from assistant.tools.notion import NotionAssignments

        registry.register(*NotionAssignments(config["notion"]["database_id"],
                                             timezone=tz).tools())
    elif config["notion"]["enabled"]:
        output_fn("(Notion tools off: NOTION_TOKEN not set)")
    web = config["web"]
    if web["enabled"]:
        from assistant.tools import web as web_tools

        registry.add_server_tools(
            *web_tools.server_tools(config["assistant"]["model"], web))
        if web["browser"]:
            registry.register(*web_tools.browser_tools())
    return registry


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


def text_confirm(input_fn=None):
    return lambda question: is_yes((input_fn or input)(f"{question} (yes/no): "))


def voice_confirm(tts, record_fn, transcriber, output_fn=print):
    """Ask out loud, record the answer, and treat only a clear yes as yes."""
    def confirm(question: str) -> bool:
        output_fn(f"Claude: {question}")
        tts.speak(f"{question} Please say yes or no.")
        recording = record_fn()
        answer = transcriber.transcribe(recording) if recording is not None else ""
        output_fn(f"You: {answer}")
        return is_yes(answer)
    return confirm


def build_wake_components(args, config: dict):
    from assistant.wakeword import (EnergyVAD, FileSource, MicSource,
                                    WakeListener, make_detector)

    wake = config["wake"]
    detector = make_detector(keyword_path=args.keyword_path or wake["keyword_path"],
                             builtin_keyword=wake["builtin_keyword"],
                             sensitivity=wake["sensitivity"])
    chime, soft_chime = audio.make_chime(), audio.make_follow_up_chime()
    chimes = wake["chime"] and not args.audio_file
    on_wake = (lambda: audio.play(chime)) if chimes else None
    on_follow_up = ((lambda: audio.play(soft_chime))
                    if chimes and wake["follow_up_chime"] else None)
    listener = WakeListener(detector, vad=EnergyVAD(wake["vad_min_threshold"]),
                            on_wake=on_wake, on_follow_up=on_follow_up,
                            silence_ms=wake["silence_ms"],
                            max_seconds=wake["max_seconds"],
                            no_speech_timeout=wake["no_speech_timeout"])
    device = audio.resolve_mic_device(wake["mic_device"])
    source = (FileSource(args.audio_file, detector.frame_length) if args.audio_file
              else MicSource(detector.frame_length, device=device))
    return listener, source, detector.phrase


def build_chat(config: dict, tools=None, memory=None) -> ClaudeChat:
    a = config["assistant"]
    client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                 timeout=a["api_timeout"])
    return ClaudeChat(client=client, model=a["model"], max_tokens=a["max_tokens"],
                      max_history=a["max_history"], tools=tools,
                      max_tool_rounds=a["max_tool_rounds"], memory=memory)


def main(argv=None) -> int:
    from assistant.config import load_config, setup_logging

    args = parse_args(argv)
    config = load_config(args.config)
    log_file = setup_logging(config)
    if args.check:
        from assistant import doctor

        return doctor.report(doctor.run_checks(config, args.keyword_path))
    if args.update:
        from assistant import updater

        return updater.main()
    log.info("Starting in %s mode (model %s)", args.mode, config["assistant"]["model"])
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env (see README.md).")
        return 1
    tts_cfg = config["tts"]
    tts_name = args.tts or tts_cfg["engine"]
    stt_cfg = config["stt"]
    try:
        if args.mode == "web":
            from assistant import webapp

            if args.timings:  # voice conversation turns: one line each
                show_voice_timings()

            return webapp.serve(config, host=args.host, port=args.port,
                                tts_name=tts_name, show_token=args.show_token)
        tools = build_tools(config)
        memory = build_memory(config)
        add_memory_tools(tools, memory)
        chat = build_chat(config, tools, memory)
        if args.mode in ("text", "speak"):
            tools.confirm = text_confirm()
            tts = make_tts(tts_name, **tts_cfg) if args.mode == "speak" else None
            run_text(chat, tts=tts)
            return 0
        tts = make_tts(tts_name, **tts_cfg)
        transcriber = Transcriber(stt_cfg["model"], stt_cfg["device"],
                                  stt_cfg["compute_type"])
        if args.mode == "voice":
            mic = audio.resolve_mic_device(config["wake"]["mic_device"])

            def record():
                return record_push_to_talk(device=mic)
            tools.confirm = voice_confirm(tts, record, transcriber)
            run_voice(chat, tts, transcriber, record_fn=record)
        else:
            listener, source, phrase = build_wake_components(args, config)
            tools.confirm = voice_confirm(
                tts, lambda: listener.record_reply(source), transcriber)
            run_wake(chat, tts, transcriber, listener, source, wake_phrase=phrase,
                     conversation_timeout=config["assistant"]["conversation_timeout"],
                     show_timings=args.timings,
                     follow_up_seconds=config["wake"]["follow_up_seconds"])
    except KeyboardInterrupt:
        print("Bye!")
    except Exception as e:  # startup problems: missing key, no mic, bad config
        log.exception("Fatal error")
        print(f"Error: {e}\n(Details in {log_file})")
        return 1
    return 0
