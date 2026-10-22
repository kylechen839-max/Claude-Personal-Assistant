"""Optional crash reports to Sentry (README "Uptime, error tracking and
backups").

Off unless the environment sets SENTRY_DSN: sentry-sdk is installed
(requirements.txt) but nothing starts without a DSN. Should the package be
missing (an older install), one warning is logged and the app runs as before.

What a report may hold is decided here, not by Sentry's defaults:
  - no personal data (send_default_pii=False), no local variables, no
    breadcrumbs, no source lines, no request bodies; no performance
    tracing at all (traces_sample_rate stays unset) and no trace headers
    (sentry-trace, baggage: they would carry the DSN's public key, the
    route and the release) on calls to Anthropic, Google, Notion...;
    integrations: Sentry's default set plus FastAPI/Starlette (crashes in
    routes), none auto-enabled;
  - before_send rebuilds every event from an allowlist: the exception
    *types* and each stack frame's file, function and line (never the
    exception message, which can quote what someone said or a tool's
    input); a log message's format string, never its arguments; the
    request's method and path only (no query string, headers, cookies,
    body or user); no server name, tags, extra (sys.argv) or modules.
    The format string only from this app's own loggers ("kwil.*"):
    asyncio's "Task exception was never retrieved" and libraries'
    log.error(exc) put an exception's text there, so for any other logger
    only its name is kept;
  - every string left is passed through config.redact (keys, tokens),
    and anything shaped like an email address is masked too.
An event that can't be cleaned is dropped, never sent as it is.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

DSN_ENV = "SENTRY_DSN"
MAX_FRAMES = 50  # per exception: the innermost ones
_started = False
_warned = False

# Sentry's own fields that carry nothing of ours
_KEEP = ("event_id", "timestamp", "level", "platform", "logger", "release",
         "environment", "sdk", "transaction", "fingerprint")
_CONTEXTS = ("runtime", "os")  # Python and OS versions only
_FRAME = ("filename", "function", "lineno", "module", "in_app")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


def init(config: dict | None = None, environ=None) -> bool:
    """Start error reporting if SENTRY_DSN is set; True if it is on. Safe
    to call more than once (cli.main and webapp.serve both do)."""
    global _started, _warned
    environ = os.environ if environ is None else environ
    dsn = str(environ.get(DSN_ENV) or "").strip()
    if not dsn:
        return False
    if _started:
        return True
    try:
        import sentry_sdk
    except ImportError:
        if not _warned:
            _warned = True
            log.warning("%s is set but sentry-sdk isn't installed; error "
                        "tracking is off (pip install -r requirements.txt)", DSN_ENV)
        return False
    from kwil import __version__

    profile = (config or {}).get("profile") or "local"
    # traces_sample_rate is left unset: even 0 counts as tracing enabled,
    # and then every outgoing HTTP call carries sentry-trace and baggage.
    # max_request_body_size="never" is needed too: without it Starlette's
    # request extractor reads bodies past their own field limits.
    sentry_sdk.init(dsn=dsn, send_default_pii=False, include_local_variables=False,
                    max_breadcrumbs=0, trace_propagation_targets=[],
                    auto_enabling_integrations=False, integrations=_web_integrations(),
                    include_source_context=False, max_request_body_size="never",
                    before_send=before_send, before_breadcrumb=_no_breadcrumb,
                    release=f"kwil-{__version__}", environment=profile)
    _started = True
    log.info("Error tracking on (Sentry, environment %s)", profile)
    return True


def _web_integrations() -> list:
    """Crashes in the web app's routes (FastAPI/Starlette): the only
    integrations added to Sentry's defaults (logging, stdlib, threads...)."""
    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except Exception:  # noqa: BLE001 - FastAPI missing: the defaults only
        return []
    return [StarletteIntegration(), FastApiIntegration()]


def _no_breadcrumb(crumb, hint=None):
    return None


def before_send(event: dict, hint=None) -> dict | None:
    """The event as Sentry gets it (see the module docstring), or None."""
    try:
        return _redact_all(_clean(event))
    except Exception:  # noqa: BLE001 - never send what couldn't be cleaned
        return None


def _clean(event: dict) -> dict:
    out = {k: event[k] for k in _KEEP if k in event}
    contexts = event.get("contexts") or {}
    out["contexts"] = {k: contexts[k] for k in _CONTEXTS if isinstance(contexts.get(k), dict)}
    exc = event.get("exception") or {}
    values = exc.get("values") if isinstance(exc, dict) else None
    if values:
        out["exception"] = {"values": [_exception(v) for v in values if isinstance(v, dict)]}
    entry = event.get("logentry")
    if isinstance(entry, dict) and isinstance(entry.get("message"), str) \
            and _own_logger(event.get("logger")):
        out["logentry"] = {"message": entry["message"]}  # the format string, no args
    request = event.get("request")
    if isinstance(request, dict):
        cleaned = {}
        if isinstance(request.get("method"), str):
            cleaned["method"] = request["method"]
        if isinstance(request.get("url"), str):
            cleaned["url"] = urlsplit(request["url"]).path  # no host, no query
        if cleaned:
            out["request"] = cleaned
    return out


def _own_logger(name) -> bool:
    """This app's loggers log fixed format strings (ids and lengths in the
    arguments); others may log an exception's text as the message."""
    return isinstance(name, str) and (name == "assistant"
                                      or name.startswith("kwil."))


def _exception(value: dict) -> dict:
    out = {k: value[k] for k in ("type", "module") if isinstance(value.get(k), str)}
    mechanism = value.get("mechanism")
    if isinstance(mechanism, dict):
        out["mechanism"] = {k: mechanism[k] for k in ("type", "handled")
                            if k in mechanism}
    frames = ((value.get("stacktrace") or {}).get("frames")) or []
    out["stacktrace"] = {"frames": [{k: f[k] for k in _FRAME if k in f}
                                    for f in frames[-MAX_FRAMES:] if isinstance(f, dict)]}
    return out


def _redact_all(value):
    from kwil.config import redact

    if isinstance(value, str):
        return _EMAIL.sub("[EMAIL]", redact(value))
    if isinstance(value, dict):
        return {k: _redact_all(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_all(v) for v in value]
    return value
