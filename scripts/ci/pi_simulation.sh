#!/usr/bin/env bash
# A simulated Raspberry Pi: run inside a debian:bookworm linux/arm64
# container (what 64-bit Raspberry Pi OS is built on: Python 3.11, aarch64).
#
#   docker run --rm --platform linux/arm64 -v "$PWD:/src:ro" -v /tmp/cache:/cache \
#       -v /tmp/out:/out debian:bookworm bash /src/scripts/ci/pi_simulation.sh
#
# As a normal user "pi" with sudo, like on the Pi, it copies the repo to
# ~/hey-claude and runs scripts/install_pi.sh end to end, then the full
# pytest suite, `python -m assistant --check`, scripts/install_service.sh
# and scripts/preflight.sh. Only what a container can't have is stubbed:
# systemd (systemctl/loginctl record their calls), the sound card (an ALSA
# "null" device) and Claude (scripts/ci/fake_claude.py; no key in CI).
# /cache keeps pip, Hugging Face and Piper downloads between runs; the
# preflight report and timings go to /out.
set -euo pipefail

SRC=/src
OUT=/out
CACHE=/cache
mkdir -p "$OUT" "$CACHE/pip" "$CACHE/hf" "$CACHE/voices"
t_start=$(date +%s)
phase() { printf '\n==== %s (%ss) ====\n' "$*" "$(( $(date +%s) - t_start ))"; }
fail() { echo "PI-SIM FAIL: $*" >&2; exit 1; }

phase "Base system: $(uname -m), $(grep PRETTY_NAME /etc/os-release)"
[[ "$(uname -m)" == "aarch64" ]] || fail "expected aarch64, got $(uname -m)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# What Raspberry Pi OS Lite already has: python3, sudo, git, ca-certificates.
apt-get install -y -qq --no-install-recommends python3 sudo git ca-certificates \
    procps shellcheck >/dev/null  # the last one only for the script lint tests

useradd --create-home --shell /bin/bash pi
echo "pi ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/010_pi-nopasswd
chown -R pi:pi "$CACHE" "$OUT"

# systemd stubs: no init system in a container. They log their calls to a
# non-sticky directory (root can't append to pi's file in /tmp).
mkdir -p /var/lib/stub-systemd && chmod 777 /var/lib/stub-systemd
for tool in systemctl loginctl; do
    cat > "/usr/local/bin/$tool" <<STUB
#!/bin/sh
echo "$tool \$*" >> /var/lib/stub-systemd/calls.log
[ "\$1 \$2" = "show-user pi" ] && echo no
exit 0
STUB
    chmod +x "/usr/local/bin/$tool"
done

cp -a "$SRC" /home/pi/hey-claude
rm -rf /home/pi/hey-claude/.venv /home/pi/hey-claude/.env /home/pi/hey-claude/logs
chown -R pi:pi /home/pi/hey-claude

# Network settings of the host (a proxy, its CA), if any, reach the pi user too.
pass_env=()
for var in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy \
           PIP_CERT REQUESTS_CA_BUNDLE SSL_CERT_FILE; do
    if [[ -n "${!var:-}" ]]; then pass_env+=("$var=${!var}"); fi
done
sudo -u pi -H "${pass_env[@]}" \
    PIP_CACHE_DIR="$CACHE/pip" HF_HOME="$CACHE/hf" OUT="$OUT" CACHE="$CACHE" \
    bash -s <<'AS_PI'
set -euo pipefail
cd ~/hey-claude
t0=$(date +%s)
phase() { printf '\n==== %s (%ss) ====\n' "$*" "$(( $(date +%s) - t0 ))"; }
fail() { echo "PI-SIM FAIL: $*" >&2; exit 1; }
timing() { echo "$1=$(( $(date +%s) - $2 ))s" >> "$OUT/timings.txt"; }

# Audio hardware stub: a "default" ALSA device that is silent.
printf 'pcm.!default { type null }\nctl.!default { type hw card 0 }\n' > ~/.asoundrc

phase "install_pi.sh (apt, venv, pip, models)"
s=$(date +%s)
./scripts/install_pi.sh
timing install_pi "$s"
PY=.venv/bin/python
"$PY" - <<'PYEOF'
import platform, sys
import ctranslate2, onnxruntime, openwakeword, piper, sounddevice  # noqa: F401
assert platform.machine() == "aarch64", platform.machine()
assert sys.version_info[:2] == (3, 11), sys.version
print("aarch64 wheels OK: onnxruntime", onnxruntime.__version__,
      "ctranslate2", ctranslate2.__version__, "| Python", platform.python_version())
PYEOF

phase "install_pi.sh again (must be a no-op)"
./scripts/install_pi.sh > /tmp/second-run.log
grep -q "Using existing virtual environment" /tmp/second-run.log \
    || fail "second run re-created the venv"
grep -q ".env already exists" /tmp/second-run.log || fail "second run replaced .env"

phase "Piper voice (cached)"
mkdir -p voices
cp -n "$CACHE"/voices/* voices/ 2>/dev/null || true
if [[ ! -f voices/en_US-lessac-medium.onnx ]]; then
    "$PY" -m assistant.tts --download-voice en_US-lessac-medium
    cp voices/* "$CACHE/voices/"
fi

phase "pytest (full suite)"
s=$(date +%s)
"$PY" -m pytest -rs
timing pytest "$s"

phase "python -m assistant --check"
# A placeholder key: --check only looks at whether it is set.
sed -i 's/^ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=ci-placeholder-not-a-key/' .env
# and a throwaway web token, so the preflight's web step runs
echo "WEB_TOKEN=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
"$PY" -m assistant --check

phase "install_service.sh (systemd stubbed)"
./scripts/install_service.sh
unit=~/.config/systemd/user/hey-claude.service
[[ -f "$unit" ]] || fail "unit not written"
grep -q "@REPO_DIR@" "$unit" && fail "unit still has @REPO_DIR@"
grep -q "ExecStart=$HOME/hey-claude/.venv/bin/python -m assistant --mode wake" "$unit" \
    || fail "unexpected ExecStart in $unit"
cat /var/lib/stub-systemd/calls.log
grep -q "enable-linger pi" /var/lib/stub-systemd/calls.log || fail "linger not enabled"
grep -q -- "--user enable hey-claude.service" /var/lib/stub-systemd/calls.log || fail "not enabled"

phase "preflight.sh (Claude faked, no keys)"
"$PY" scripts/ci/fake_claude.py 8999 &
fake=$!
s=$(date +%s)
# git: a CI checkout is detached; pytest ran above; text-tools and the live
# tests need Kyle's Google/Notion accounts. Everything else runs for real:
# the wake word, Whisper and TTS, and --mode web with a text, an audio
# and a WebSocket voice turn.
ANTHROPIC_BASE_URL=http://127.0.0.1:8999 \
    ./scripts/preflight.sh --skip git,pytest,pytest-live,text-tools \
    | tee "$OUT/preflight-console.txt"
timing preflight "$s"
kill "$fake"
cp logs/preflight-*.txt "$OUT/"
grep -q "^PASS  wake-e2e" "$OUT/preflight-console.txt" || fail "wake-e2e did not pass"
for row in tts-piper "web: healthz" "web: auth required" "web: login" \
           "web: text turn" "web: audio turn" "web: voice turn"; do
    grep -q "^PASS  $row" "$OUT/preflight-console.txt" || fail "$row did not pass"
done
timing total "$t0"
AS_PI

phase "Done"
cat "$OUT/timings.txt"
# Let the CI runner's (different) user read the cache back to save it.
chmod -R a+rX "$CACHE" "$OUT"
