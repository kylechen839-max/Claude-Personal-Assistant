# Kwil, cloud edition: the phone web app (python -m kwil --mode web)
# on a headless server. Build: docker build -t kwil .
# Run:   docker run -p 10000:10000 -e PORT=10000 --env-file .env kwil
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

# Speech models are baked in at build time so a cold start downloads nothing
# (HF_HUB_OFFLINE=1 below: nothing is fetched at run time either).
# Both Whisper sizes, so WHISPER_MODEL=tiny.en works without a rebuild.
ARG WHISPER_MODELS="base.en tiny.en"
ARG PIPER_VOICE=en_US-lessac-medium
ARG PIPER_VOICE_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
# Chinese-speaking family members ([family] zh_stt_model / zh_piper_voice,
# loaded only when one of them first talks): multilingual Whisper "small"
# (about 480 MB on disk) and the Mandarin Piper voice (about 60 MB).
# FAMILY_ZH=0 leaves them out (a smaller image; their speech then uses the
# phone's own voice and Whisper can't hear Chinese).
ARG FAMILY_ZH=1
ARG ZH_WHISPER_MODEL=small
ARG ZH_PIPER_VOICE=zh_CN-huayan-medium
ARG ZH_PIPER_VOICE_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium
# FETCH_MODELS=0 skips the downloads (only for build tests without internet
# access to Hugging Face; the app then falls back to espeak-ng).
ARG FETCH_MODELS=1
RUN if [ "$FETCH_MODELS" = "1" ]; then \
      if [ "$FAMILY_ZH" = "1" ]; then WHISPER_MODELS="$WHISPER_MODELS $ZH_WHISPER_MODEL"; fi \
      && python -c "import sys; from faster_whisper.utils import download_model; \
[print('Whisper', m, '->', download_model(m)) for m in sys.argv[1:]]" $WHISPER_MODELS \
      && mkdir -p /app/voices \
      && python -c "import sys, urllib.request; \
[urllib.request.urlretrieve(f'{sys.argv[1]}/{sys.argv[2]}{ext}', f'/app/voices/{sys.argv[2]}{ext}') \
 for ext in ('.onnx', '.onnx.json')]" "$PIPER_VOICE_URL" "$PIPER_VOICE" \
      && if [ "$FAMILY_ZH" = "1" ]; then python -c "import sys, urllib.request; \
[urllib.request.urlretrieve(f'{sys.argv[1]}/{sys.argv[2]}{ext}', f'/app/voices/{sys.argv[2]}{ext}') \
 for ext in ('.onnx', '.onnx.json')]" "$ZH_PIPER_VOICE_URL" "$ZH_PIPER_VOICE"; fi \
      && ls -la /app/voices; \
    fi \
    && mkdir -p /opt/models /app/voices && chmod -R a+rX /opt/models /app/voices

COPY config.toml ./
COPY kwil ./kwil
# the package's name before the Kwil rebrand: a start command that still runs
# `python -m assistant` keeps working (assistant/__init__.py)
COPY assistant ./assistant
COPY scripts/measure_memory.py scripts/ws_smoke.py ./scripts/

# The app runs as an unprivileged user that can't modify the app or the
# models. The entrypoint starts as root only to hand the persistent disk
# (/data: conversation history, lecture audio, job state, and /data/users:
# family members' history and Google tokens) to that user, then
# drops all privileges for good (setpriv --no-new-privs).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data && chmod 700 /data
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV ASSISTANT_PROFILE=server \
    DATA_DIR=/data \
    HISTORY_PATH=/data/history.sqlite \
    HF_HUB_OFFLINE=1 \
    HOME=/tmp \
    PORT=10000
EXPOSE 10000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "kwil", "--mode", "web"]
