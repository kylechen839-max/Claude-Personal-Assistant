"""Peak memory of the cloud server's speech work, for picking a server size.

Run inside the Docker image (CI does this on every push):

    docker run --rm kwil python scripts/measure_memory.py --model base.en

In one fresh process it imports everything the web server loads, renders a
5 s clip of speech with Piper, transcribes it with Whisper, and renders a
reply with Piper, printing the process's resident memory after each step
(current and peak, from /proc/self/status) as JSON lines. `--clip-out`
also saves the clip, to upload to a running server.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUESTION = ("Hey Claude, what's on my calendar tomorrow? "
            "And do I have any assignments due this week?")
REPLY = ("Tomorrow you have Calculus at nine, lunch with Sam at noon, and the "
         "dentist at four thirty. Your physics lab report is due Thursday.")


def rss_mb() -> dict:
    """Current (VmRSS) and peak (VmHWM) resident memory in MB."""
    out = {}
    with open("/proc/self/status") as f:
        for line in f:
            key, _, value = line.partition(":")
            if key in ("VmRSS", "VmHWM"):
                out[key] = round(int(value.split()[0]) / 1024, 1)
    return {"rss_mb": out.get("VmRSS"), "peak_mb": out.get("VmHWM")}


def report(step: str, started: float, **extra) -> None:
    print(json.dumps({"step": step, **rss_mb(),
                      "seconds": round(time.monotonic() - started, 2), **extra}),
          flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="base.en", help="Whisper model")
    parser.add_argument("--voice", default="voices/en_US-lessac-medium.onnx")
    parser.add_argument("--seconds", type=float, default=5.0, help="clip length")
    parser.add_argument("--clip-out", help="also save the clip as this .wav")
    args = parser.parse_args(argv)

    t = time.monotonic()
    report("python", t)
    # what the server imports (anthropic, Google, Notion, Pinecone, FastAPI, PyAV)
    import anthropic  # noqa: F401
    import av  # noqa: F401
    import googleapiclient.discovery  # noqa: F401
    import notion_client  # noqa: F401
    import numpy as np
    import pinecone  # noqa: F401
    import uvicorn  # noqa: F401

    from kwil import audio, cloud, webapp  # noqa: F401
    from kwil.stt import Transcriber
    from kwil.tts import EspeakTTS, PiperTTS, piper_voice_problem
    report("server imports", t)

    t = time.monotonic()
    problem = piper_voice_problem(args.voice)
    tts = PiperTTS(args.voice) if problem is None else EspeakTTS()
    engine = "piper" if problem is None else "espeak-ng"
    wav = webapp.render_wav(tts, QUESTION)
    import io

    samples, sr = audio.read_wav(io.BytesIO(wav))
    clip = audio.resample(samples, sr)
    n = int(args.seconds * audio.SAMPLE_RATE)
    clip = np.concatenate([clip, audio.silence(max(0.0, args.seconds))])[:n]
    if args.clip_out:
        audio.write_wav(args.clip_out, clip)
    report("tts clip", t, engine=engine, clip_seconds=round(len(clip) / audio.SAMPLE_RATE, 2))

    t = time.monotonic()
    transcriber = Transcriber(args.model, "cpu", "int8")
    _ = transcriber.model
    report("whisper loaded", t, model=args.model)

    t = time.monotonic()
    text = transcriber.transcribe(clip)
    report("transcribed", t, model=args.model, transcript=text)

    t = time.monotonic()
    reply = webapp.render_wav(tts, REPLY)
    report("reply rendered", t, engine=engine, wav_kb=len(reply) // 1024)

    peak = rss_mb()["peak_mb"]
    cgroup = None
    for path in ("/sys/fs/cgroup/memory.peak",
                 "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            cgroup = round(int(Path(path).read_text()) / 2**20, 1)
            break
        except (OSError, ValueError):
            pass
    print(json.dumps({"step": "summary", "model": args.model, "tts": engine,
                      "peak_rss_mb": peak, "cgroup_peak_mb": cgroup,
                      "cpus": os.cpu_count()}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
