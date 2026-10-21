#!/bin/sh
# Container start: make the persistent disk (/data, mounted by Render at run
# time, usually owned by root) belong to the app user, then run the app as
# that user with no way to regain privileges. Started as another user
# (docker run --user ...), it just runs the app.
set -e
APP_UID=10001
if [ "$(id -u)" = 0 ]; then
    if [ -d "${DATA_DIR:-/data}" ]; then
        chown "$APP_UID:$APP_UID" "${DATA_DIR:-/data}"
        chmod 700 "${DATA_DIR:-/data}"
    fi
    # Dropping the capability bounding set needs CAP_SETPCAP, which some hosts
    # (Render) don't grant; drop it only where the runtime allows.
    BOUNDING=
    if setpriv --bounding-set=-all true 2>/dev/null; then
        BOUNDING=--bounding-set=-all
    fi
    exec setpriv --reuid="$APP_UID" --regid="$APP_UID" --clear-groups \
        --no-new-privs --inh-caps=-all $BOUNDING -- "$@"
fi
exec "$@"
