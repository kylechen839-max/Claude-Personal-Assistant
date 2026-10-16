"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse

import anthropic

from assistant import audio
from assistant.chat import ClaudeChat
from assistant.stt import Transcriber, record_push_to_talk
from assistant.tts import make_tts

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}


def is_exit(text: str) -> bool:
    return text.lower().strip(" .!?,") in EXIT_WORDS


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
    chat = ClaudeChat()
    if args.mode == "text":
        run_text(chat)
    elif args.mode == "speak":
        run_text(chat, tts=make_tts(args.tts))
    elif args.mode == "voice":
        run_voice(chat, make_tts(args.tts), Transcriber())
    else:
        listener, source, phrase = build_wake_components(args)
        run_wake(chat, make_tts(args.tts), Transcriber(), listener, source,
                 wake_phrase=phrase)
    return 0
