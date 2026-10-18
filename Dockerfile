# Hey Claude, cloud edition: the phone web app (python -m assistant --mode web)
# on a headless server. Build: docker build -t hey-claude .
# Run:   docker run -p 10000:10000 -e PORT=10000 --env-file .env hey-claude
# Secrets come from the environment only (see README "Run it in the cloud").
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/models/huggingface

# espeak-ng: TTS fallback if the Piper voice is missing. ffmpeg: decoding
# phone recordings (PyAV also bundles its own copy).
RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# openWakeWord isn't installed: the server has no microphone or wake word.
RUN pip install -r requirements.txt

# Speech models are baked in at build time so a cold start downloads nothing.
# Both Whisper sizes, so WHISPER_MODEL=tiny.en works without a rebuild.
ARG WHISPER_MODELS="base.en tiny.en"
ARG PIPER_VOICE=en_US-lessac-medium
ARG PIPER_VOICE_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
# FETCH_MODELS=0 skips the downloads (only for build tests without internet
# access to Hugging Face; the app then falls back to espeak-ng).
ARG FETCH_MODELS=1
RUN if [ "$FETCH_MODELS" = "1" ]; then \
      python -c "import sys; from faster_whisper.utils import download_model; \
[print('Whisper', m, '->', download_model(m)) for m in sys.argv[1:]]" $WHISPER_MODELS \
      && mkdir -p /app/voices \
      && python -c "import sys, urllib.request; \
[urllib.request.urlretrieve(f'{sys.argv[1]}/{sys.argv[2]}{ext}', f'/app/voices/{sys.argv[2]}{ext}') \
 for ext in ('.onnx', '.onnx.json')]" "$PIPER_VOICE_URL" "$PIPER_VOICE" \
      && ls -la /app/voices; \
    fi

COPY config.toml ./
COPY assistant ./assistant
COPY scripts/measure_memory.py ./scripts/measure_memory.py

# Run as an unprivileged user that can't modify the app or the models.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app

ENV ASSISTANT_PROFILE=server \
    HF_HUB_OFFLINE=1 \
    HOME=/tmp \
    PORT=10000
EXPOSE 10000
CMD ["python", "-m", "assistant", "--mode", "web"]
