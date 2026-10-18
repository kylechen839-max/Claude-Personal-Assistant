#!/usr/bin/env bash
# Smoke test and memory measurement of the cloud Docker image (run by CI).
#   scripts/container_smoke.sh IMAGE
# Starts the server like Render does, checks /healthz and the auth wall,
# runs one real spoken turn (Whisper + Piper inside the container; Claude
# answers "API key isn't working" because the key is a dummy), then measures
# peak memory for base.en and tiny.en. Prints no secrets.
set -euo pipefail
IMAGE=${1:-hey-claude:ci}
PORT=10000
URL=http://localhost:$PORT
WORK=$(mktemp -d)
trap 'docker logs hc > "$WORK/server.log" 2>&1 || true; docker rm -f hc >/dev/null 2>&1 || true' EXIT
umask 077
python3 -c 'import secrets; print("WEB_TOKEN=" + secrets.token_urlsafe(32))' > "$WORK/env"
echo "ANTHROPIC_API_KEY=ci-dummy-key" >> "$WORK/env"
TOKEN=$(sed -n 's/^WEB_TOKEN=//p' "$WORK/env")
summary() { echo "$*" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"; }

# /data as on Render: a disk mounted at run time, owned by root
docker run -d --name hc -p $PORT:$PORT --env-file "$WORK/env" \
    --mount type=tmpfs,destination=/data ${DOCKER_RUN_ARGS:-} "$IMAGE" >/dev/null
for _ in $(seq 1 60); do curl -sf $URL/healthz >/dev/null && break; sleep 1; done

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
test "$(curl -sf $URL/healthz)" = ok
test "$(status $URL/api/state)" = 401                       # auth required
test "$(status $URL/silence.wav)" = 401
test "$(status $URL/)" = 200                                # login page
test "$(status $URL/manifest.webmanifest)" = 200
test "$(status -H 'Content-Type: application/json' -d '{"token":"wrong-token"}' $URL/api/login)" = 401
test "$(status -c "$WORK/cookies" -H 'Content-Type: application/json' \
        -d "{\"token\":\"$TOKEN\"}" $URL/api/login)" = 200
test "$(status -b "$WORK/cookies" $URL/api/state)" = 200     # signed in
head -c 5000000 /dev/zero > "$WORK/big.wav"                 # over the 4 MB cap
test "$(status -b "$WORK/cookies" -F "audio=@$WORK/big.wav" $URL/api/turn)" = 413
echo "auth checks passed"
# the server runs unprivileged and owns the disk
test "$(docker exec hc awk '/^Uid:/ {print $2}' /proc/1/status)" = 10001
test "$(docker exec hc stat -c %u /data)" = 10001
docker exec -u 10001 hc sh -c 'echo ok > /data/probe && rm /data/probe'
echo "user and /data checks passed"

docker exec -u 10001 hc python -m assistant --check
[ "${SMOKE_ONLY:-}" = 1 ] && exit 0

# One spoken turn through the real server: upload a 5 s Piper clip.
docker exec -u 10001 hc python scripts/measure_memory.py --model tiny.en \
    --clip-out /tmp/clip.wav >/dev/null
docker cp hc:/tmp/clip.wav "$WORK/clip.wav"
docker restart hc >/dev/null   # measure the server on its own
for _ in $(seq 1 60); do curl -sf $URL/healthz >/dev/null && break; sleep 1; done
sleep 20  # let the warm-up load Whisper and the voice
curl -s -b "$WORK/cookies" -F "audio=@$WORK/clip.wav;type=audio/wav" \
    $URL/api/turn > "$WORK/turn.ndjson"
cat "$WORK/turn.ndjson"
grep -qi '"transcript".*calendar' "$WORK/turn.ndjson"
AUDIO=$(python3 -c 'import json,sys; print([e for e in map(json.loads, open(sys.argv[1])) if e["type"]=="reply"][0]["audio"])' "$WORK/turn.ndjson")
test "$(status -b "$WORK/cookies" "$URL$AUDIO")" = 200
SERVER_PEAK=$(docker exec hc cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0)
SERVER_RSS=$(docker exec hc sh -c 'grep -E "VmHWM" /proc/1/status' | awk '{print $2}')
summary "### Cloud image memory"
summary ""
summary "Server after one spoken turn (base.en + Piper): peak RSS $((SERVER_RSS / 1024)) MB, container peak $((SERVER_PEAK / 1048576)) MB"

for MODEL in base.en tiny.en; do
    docker run --rm "$IMAGE" python scripts/measure_memory.py --model $MODEL \
        | tee "$WORK/$MODEL.jsonl"
    summary "- $MODEL standalone: $(tail -1 "$WORK/$MODEL.jsonl")"
    for LIMIT in 512m; do
        if docker run --rm --memory=$LIMIT --memory-swap=$LIMIT "$IMAGE" \
            python scripts/measure_memory.py --model $MODEL > "$WORK/limit.jsonl" 2>&1; then
            summary "- $MODEL under a $LIMIT limit: survived, $(tail -1 "$WORK/limit.jsonl")"
        else
            summary "- $MODEL under a $LIMIT limit: FAILED (exit $?, likely out of memory)"
        fi
    done
done
