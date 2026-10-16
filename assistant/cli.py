"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse

import anthropic

from assistant import audio
from assistant.chat import ClaudeChat
from assistant.tools import ToolRegistry
from assistant.stt import Transcriber, record_push_to_talk
from assistant.tts import make_tts

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct",
             "affirmative", "confirm", "confirmed", "absolutely", "definitely"}
NO_WORDS = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "wait"}
DEFAULT_NOTION_DB = "7eefabe3-0226-47a8-a287-584be0d2ea9b"  # Fall 2026 Assignments


def is_exit(text: str) -> bool:
    return text.lower().strip(" .!?,") in EXIT_WORDS


def is_yes(text: str) -> bool:
    """Strict yes: a yes word and no negation ('yes, but no' -> False)."""
    import re

    words = set(re.findall(r"[a-z']+", text.lower()))
    phrase = " ".join(re.findall(r"[a-z']+", text.lower()))
    yes = bool(words & YES_WORDS) or phrase in {"go ahead", "do it", "please do"}
    return yes and not (words & NO_WORDS)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant",
                                     description="Hey Claude voice assistant")
    parser.add_argument("--mode", choices=["text", "speak", "voice", "wake"],
                        default="wake",
                        help="text: type/read; speak: type/listen; voice: "
                             "push-to-talk; wake: always listening (default)")
    parser.add_argument("--audio-file",
                        help="wake mode: feed this .wav through the wake-word "
                             "pipeline instead of the live mic, then exit")
    parser.add_argument("--tts", default="pyttsx3",
                        choices=["pyttsx3", "piper", "print"])
    parser.add_argument("--keyword-path",
                        help="Porcupine .ppn file for the 'Hey Claude' wake word")
    return parser.parse_args(argv)


def get_reply(chat: ClaudeChat, user_text: str, output_fn=print) -> str:
    """Ask Claude; on failure return a short sentence that can be spoken."""
    try:
        return chat.ask(user_text)
    except anthropic.APIConnectionError as e:
        output_fn(f"Connection error: {e}")
        return "Sorry, I can't reach the internet right now."
    except anthropic.APIError as e:
        output_fn(f"API error: {e}")
        return "Sorry, something went wrong talking to Claude."


def run_text(chat: ClaudeChat, input_fn=input, output_fn=print,
             tts=None) -> None:
    """Typed input; replies printed, and spoken too if a TTS engine is given."""
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
    output_fn("Bye!")


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
    output_fn("Bye!")


def run_wake(chat: ClaudeChat, tts, transcriber, listener, source,
             output_fn=print, wake_phrase: str = "Hey Claude") -> None:
    """Always listening. 'Goodbye' ends the conversation (history is reset)
    but keeps listening; Ctrl+C quits. With a file source, stops at its end."""
    output_fn(f"Listening for '{wake_phrase}'... (Ctrl+C to quit)")
    while True:
        try:
            woke, recording = listener.listen(source)
        except KeyboardInterrupt:
            break
        if not woke:
            break  # audio file finished
        if recording is None:
            output_fn("(heard the wake word but no command)")
            continue
        user_text = transcriber.transcribe(recording)
        output_fn(f"You: {user_text}")
        if not user_text:
            continue
        if is_exit(user_text):
            chat.reset()
            tts.speak("Goodbye!")
            continue
        reply = get_reply(chat, user_text, output_fn)
        output_fn(f"Claude: {reply}")
        tts.speak(reply)
    output_fn("Bye!")


def build_tools(output_fn=print) -> ToolRegistry:
    """Register every service whose credentials are available."""
    import os
    from pathlib import Path

    registry = ToolRegistry()
    creds = os.environ.get("GOOGLE_CREDENTIALS", "credentials.json")
    token = os.environ.get("GOOGLE_TOKEN", "token.json")
    if Path(creds).exists() or Path(token).exists():
        from assistant.tools.calendar import GoogleCalendar

        registry.register(*GoogleCalendar(credentials_path=creds,
                                          token_path=token).tools())
    else:
        output_fn(f"(Calendar tools off: {creds} not found)")
    if os.environ.get("NOTION_TOKEN"):
        from assistant.tools.notion import NotionAssignments

        db = os.environ.get("NOTION_ASSIGNMENTS_DB", DEFAULT_NOTION_DB)
        registry.register(*NotionAssignments(db).tools())
    else:
        output_fn("(Notion tools off: NOTION_TOKEN not set)")
    return registry


def text_confirm(input_fn=input):
    return lambda question: is_yes(input_fn(f"{question} (yes/no): "))


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


def build_wake_components(args):
    from assistant.wakeword import (FileSource, MicSource, WakeListener,
                                    make_detector)

    detector = make_detector(keyword_path=args.keyword_path)
    chime = audio.make_chime()
    on_wake = None if args.audio_file else (lambda: audio.play(chime))
    listener = WakeListener(detector, on_wake=on_wake)
    source = FileSource(args.audio_file) if args.audio_file else MicSource(
        detector.frame_length)
    return listener, source, detector.phrase


def main(argv=None) -> int:
    args = parse_args(argv)
    tools = build_tools()
    chat = ClaudeChat(tools=tools)
    if args.mode == "text":
        tools.confirm = text_confirm()
        run_text(chat)
    elif args.mode == "speak":
        tools.confirm = text_confirm()
        run_text(chat, tts=make_tts(args.tts))
    elif args.mode == "voice":
        tts, transcriber = make_tts(args.tts), Transcriber()
        tools.confirm = voice_confirm(tts, record_push_to_talk, transcriber)
        run_voice(chat, tts, transcriber)
    else:
        listener, source, phrase = build_wake_components(args)
        tts, transcriber = make_tts(args.tts), Transcriber()
        tools.confirm = voice_confirm(
            tts, lambda: listener.record_reply(source), transcriber)
        run_wake(chat, tts, transcriber, listener, source, wake_phrase=phrase)
    return 0
