#!/usr/bin/env bash
# One-step setup on a Raspberry Pi (Pi 4/5, 64-bit Raspberry Pi OS Bookworm).
# Safe to re-run: it only installs or creates what is missing.
#
#   ./scripts/install_pi.sh
#
# Environment overrides:
#   PYTHON=python3.11   interpreter used to create .venv (default: python3)
#   SKIP_APT=1          don't install system packages (already done / not Debian)
#   SKIP_MODELS=1       don't pre-download the Whisper and wake-word models
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PYTHON="${PYTHON:-python3}"
VENV="$REPO_DIR/.venv"
APT_PACKAGES=(
    python3-venv python3-dev  # virtual environment
    libportaudio2             # sounddevice (mic and speaker)
    espeak-ng libespeak1      # pyttsx3 voice on Linux
    alsa-utils                # arecord / aplay / speaker-test for checking audio
)

step() { printf '\n==> %s\n' "$*"; }

# ---- 1. system packages ----
if [[ "${SKIP_APT:-0}" == "1" ]]; then
    step "Skipping system packages (SKIP_APT=1)"
elif ! command -v apt-get >/dev/null 2>&1; then
    step "apt-get not found; install these yourself: ${APT_PACKAGES[*]}"
else
    missing=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed" \
            || missing+=("$pkg")
    done
    if ((${#missing[@]})); then
        step "Installing system packages: ${missing[*]}"
        SUDO=""
        [[ "$(id -u)" -ne 0 ]] && SUDO="sudo"
        $SUDO apt-get update
        $SUDO apt-get install -y "${missing[@]}"
    else
        step "System packages already installed"
    fi
fi

# ---- 2. virtual environment ----
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "Python 3.10+ is required; $PYTHON is $("$PYTHON" --version 2>&1)." >&2
    exit 1
fi
if [[ -x "$VENV/bin/python" ]]; then
    step "Using existing virtual environment .venv"
else
    step "Creating virtual environment .venv ($("$PYTHON" --version 2>&1))"
    "$PYTHON" -m venv "$VENV"
fi
PY="$VENV/bin/python"

# ---- 3. Python packages ----
step "Installing Python packages (first run takes a while on a Pi)"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
# openWakeWord's Linux metadata requires tflite-runtime, which has no wheels
# for newer Pythons; only its ONNX backend is used, so skip its dependencies.
"$PY" -m pip install --no-deps "openwakeword>=0.6"

# ---- 4. secrets file ----
if [[ -f .env ]]; then
    step ".env already exists (left unchanged)"
else
    step "Creating .env from .env.example"
    cp .env.example .env
fi
chmod 600 .env

# ---- 5. models (optional; otherwise downloaded on first run) ----
if [[ "${SKIP_MODELS:-0}" == "1" ]]; then
    step "Skipping model downloads (SKIP_MODELS=1)"
else
    step "Downloading the Whisper and wake-word models (needs internet)"
    "$PY" - <<'PYEOF' || echo "Model download failed; they will download on first run instead."
import sys

from assistant.config import load_config
from assistant.wakeword import _ensure_feature_models
from faster_whisper.utils import download_model

cfg = load_config()
try:
    download_model(cfg["stt"]["model"])
    _ensure_feature_models(cfg["wake"]["builtin_keyword"])
except Exception as e:
    print(f"{type(e).__name__}: {e}")
    sys.exit(1)
print("Models ready.")
PYEOF
fi

# ---- next steps ----
cat <<EOM

Done. Next steps:
  1. Put your keys in .env:          nano $REPO_DIR/.env
  2. Google Calendar: copy credentials.json and token.json from your PC into
     $REPO_DIR (or sign in with: .venv/bin/python -m assistant.tools.calendar --headless)
  3. Optional, a much better voice (Piper, about 60 MB):
       .venv/bin/python -m assistant --download-voice en_US-lessac-medium
     (or copy voices/ from your PC); [tts] engine = "auto" then uses it.
     Kokoro is more natural but too slow for a Pi, so Piper is preferred here.
  4. Check the setup:                .venv/bin/python -m assistant --check
  5. Try it:                         .venv/bin/python -m assistant
  6. Start it at boot:               ./scripts/install_service.sh
EOM
