"""
claude_assistant.py - PC prototype of a voice assistant using the Anthropic API.

STAGES (pick with --mode):
  text   : type to Claude, read replies in the terminal          (Stage 1)
  speak  : type to Claude, replies are spoken aloud              (Stage 2)
  voice  : push-to-talk with your mic, replies spoken aloud      (Stage 3)

SETUP (Windows, Python 3.10+):
  pip install anthropic pyttsx3 sounddevice numpy faster-whisper
  setx ANTHROPIC_API_KEY "your-key-here"     (then open a new terminal)

RUN:
  python claude_assistant.py --mode text
  python claude_assistant.py --mode speak
  python claude_assistant.py --mode voice

Next stages to add later: wake word (Picovoice Porcupine), tools (Google
Calendar / Notion APIs), and Pinecone for long-term memory.
"""

import argparse
import os
import sys

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
SYSTEM_PROMPT = (
    "You are Kyle's personal voice assistant. Keep answers short and "
    "conversational (1-3 sentences) since they may be read aloud."
)

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment
history = []  # conversation memory for this session


def ask_claude(user_text: str) -> str:
    history.append({"role": "user", "content": user_text})
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=history,
    )
    reply = "".join(b.text for b in response.content if b.type == "text")
    history.append({"role": "assistant", "content": reply})
    return reply


# ---------- Stage 2: text to speech ----------
_tts_engine = None


def speak(text: str) -> None:
    global _tts_engine
    import pyttsx3

    if _tts_engine is None:
        _tts_engine = pyttsx3.init()
        _tts_engine.setProperty("rate", 185)
    _tts_engine.say(text)
    _tts_engine.runAndWait()


# ---------- Stage 3: push-to-talk speech to text ----------
_whisper_model = None
SAMPLE_RATE = 16000


def record_push_to_talk():
    import numpy as np
    import sounddevice as sd

    input("\n[Press Enter to start talking]")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=callback):
        input("[Recording... press Enter to stop]")
    if not frames:
        return None
    return np.concatenate(frames, axis=0).flatten()


def transcribe(audio) -> str:
    global _whisper_model
    from faster_whisper import WhisperModel

    if _whisper_model is None:
        print("(Loading Whisper model, first run may take a minute...)")
        _whisper_model = WhisperModel("base.en", device="cpu",
                                      compute_type="int8")
    segments, _ = _whisper_model.transcribe(audio, language="en")
    return " ".join(s.text.strip() for s in segments).strip()


# ---------- Main loop ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["text", "speak", "voice"],
                        default="text")
    args = parser.parse_args()

    print(f"Claude assistant ({args.mode} mode). Say or type 'quit' to exit.")
    while True:
        try:
            if args.mode == "voice":
                audio = record_push_to_talk()
                if audio is None:
                    continue
                user_text = transcribe(audio)
                print(f"You: {user_text}")
            else:
                user_text = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_text:
            continue
        if user_text.lower().strip(" .!") in {"quit", "exit", "goodbye"}:
            break

        try:
            reply = ask_claude(user_text)
        except anthropic.APIError as e:
            print(f"API error: {e}")
            continue

        print(f"Claude: {reply}")
        if args.mode in ("speak", "voice"):
            speak(reply)

    print("Bye!")


if __name__ == "__main__":
    sys.exit(main())
