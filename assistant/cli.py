"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse

import anthropic

from assistant.chat import ClaudeChat
from assistant.stt import Transcriber, record_push_to_talk
from assistant.tts import make_tts

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}


def is_exit(text: str) -> bool:
    return text.lower().strip(" .!?,") in EXIT_WORDS


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant",
                                     description="Hey Claude voice assistant")
    parser.add_argument("--mode", choices=["text", "speak", "voice"], default="text")
    return parser.parse_args(argv)


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
        try:
            reply = chat.ask(user_text)
        except anthropic.APIError as e:
            output_fn(f"API error: {e}")
            continue
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
        try:
            reply = chat.ask(user_text)
        except anthropic.APIError as e:
            output_fn(f"API error: {e}")
            continue
        output_fn(f"Claude: {reply}")
        tts.speak(reply)
    output_fn("Bye!")


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "voice":
        run_voice(ClaudeChat(), make_tts("pyttsx3"), Transcriber())
    else:
        tts = make_tts("pyttsx3") if args.mode == "speak" else None
        run_text(ClaudeChat(), tts=tts)
    return 0
