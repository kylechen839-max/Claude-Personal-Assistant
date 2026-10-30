""""Think harder": one turn answered by the main model with more room to
think, asked for per message (the app's toggle or a long-press on Send,
or the words "think hard about..." / "仔细想想" at the start of a
message, typed or spoken).

The flagged turn (ClaudeChat.ask(think=True), _start_think):
- goes to `[assistant] model` whatever the router would pick (router.py,
  reason "think"). That is the conversation's main model, so a family
  member or beta tester never gets a model their chat doesn't already
  use, and the router's rules apply as for any main-model turn: after
  it the conversation stays on the main model (the fast model can't read
  the main model's thinking), and a switch from the fast model forgets
  the earlier turns' thinking (chat._run);
- has `[assistant] think_max_tokens` (at least the turn's usual ceiling;
  at most MAX_UNSTREAMED_TOKENS when not streamed: the SDK refuses a
  longer non-streamed request);
- asks for thinking explicitly: `thinking={"type": "adaptive"}` (the only
  shape the Claude 5 models take; `budget_tokens` is a 400 there) and
  `output_config={"effort": think_effort}` ("xhigh" by default; "" sends
  none). Sources: platform.claude.com/docs/en/build-with-claude/thinking,
  .../effort (fetched 2026-10-30); tests/contract_fakes.py checks both.

It is never sticky: the next turn is an ordinary one. A different effort
or thinking setting changes the rendered prompt, so the think turn and
the one after it miss the prompt cache once (accepted).

Cost is capped per person per day: `[assistant] think_per_day` (0 = no
limit), counted in quotas.UserQuotas (an invited person's in Family.quotas,
the owner's in one per process, owner_gate). Over the cap the turn is
answered normally and the app says why ("think_limit"). Counts live in
memory, like the other limits: a restart resets them. Logs say "on" or
"refused", never the words.
"""

from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16000
DEFAULT_EFFORT = "xhigh"
DEFAULT_PER_DAY = 30
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
# anthropic's SDK refuses a non-streamed request it expects to take over
# 10 minutes (about 21,000 tokens); streamed turns aren't limited
MAX_UNSTREAMED_TOKENS = 21000
MAX_TOKENS_CEILING = 64000

# at the start of the message (a wake word or "please" may come first)
_ASKED = re.compile(
    r"^\W*(?:(?:hey\s+)?(?:kwil|quill|claude)\W+)?(?:(?:ok(?:ay)?|so|please)\W+)*"
    r"(?:think\s+(?:really\s+|very\s+|extra\s+)?hard(?:er)?|think\s+(?:it\s+)?through"
    r"|think\s+carefully)\b",
    re.IGNORECASE)
_ASKED_ZH = re.compile(r"^\W*(?:请|你)?(?:仔细|认真|好好)(?:地)?想(?:想|一想|一下)")


def asked(text: str) -> bool:
    """Do these words start with "think hard (about)..." or "仔细想想"?"""
    text = str(text or "")[:200]
    return bool(_ASKED.search(text) or _ASKED_ZH.search(text))


def request_options(chat) -> dict:
    """The think turn's request parameters for `chat` (a ClaudeChat)."""
    ceiling = max(int(chat.think_max_tokens or 0), chat._max_tokens(chat.model))
    options = {"max_tokens": min(ceiling, MAX_TOKENS_CEILING),
               "thinking": {"type": "adaptive"}}
    effort = str(chat.think_effort or "").strip().lower()
    if effort in EFFORTS and effort:
        options["output_config"] = {"effort": effort}
    return options


def options_from_config(config: dict) -> dict:
    """ClaudeChat's think_max_tokens and think_effort from [assistant]."""
    a = config["assistant"]
    effort = str(a.get("think_effort", DEFAULT_EFFORT) or "").strip().lower()
    if effort not in EFFORTS:
        log.warning("[assistant] think_effort must be one of %s; using %s",
                    "/".join(e for e in EFFORTS if e), DEFAULT_EFFORT)
        effort = DEFAULT_EFFORT
    return {"think_max_tokens": int(a.get("think_max_tokens", DEFAULT_MAX_TOKENS)),
            "think_effort": effort}


# ---------- the owner's daily count ----------
OWNER = "owner"  # the owner's key in his own UserQuotas
_owner_quotas = None
_owner_lock = threading.Lock()


def owner_gate(config: dict):
    """think_gate for the owner's chats in this process: one more think
    turn today (his [assistant] timezone's day)? Counts it when yes."""
    from kwil.quotas import UserQuotas

    global _owner_quotas
    per_day = int(config["assistant"].get("think_per_day", DEFAULT_PER_DAY))
    zone = config["assistant"].get("timezone") or None
    with _owner_lock:
        if _owner_quotas is None or _owner_quotas.think_per_day != per_day:
            _owner_quotas = UserQuotas(0, 0, 0, think_per_day=per_day,
                                       timezone_of=lambda uid: zone)
        quotas = _owner_quotas
    return lambda: quotas.admit_think(OWNER)


def reset_owner() -> None:
    """Forget the owner's count (tests)."""
    global _owner_quotas
    with _owner_lock:
        _owner_quotas = None
