"""Command-line entry point: python -m assistant."""

from __future__ import annotations

import argparse

import anthropic

from assistant.chat import ClaudeChat

EXIT_WORDS = {"quit", "exit", "goodbye", "bye"}


def is_exit(text: str) -> bool:
    return text.lower().strip(" .!?,") in EXIT_WORDS


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assistant",
                                     description="Hey Claude voice assistant")
    parser.add_argument("--mode", choices=["text"], default="text")
    return parser.parse_args(argv)


def run_text(chat: ClaudeChat, input_fn=input, output_fn=print) -> None:
    output_fn("Claude assistant (text mode). Type 'quit' to exit.")
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
    output_fn("Bye!")


def main(argv=None) -> int:
    parse_args(argv)
    run_text(ClaudeChat())
    return 0
