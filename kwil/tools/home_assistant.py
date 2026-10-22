"""Home Assistant tools: list devices, read a state, control a device.

Talks to the Home Assistant REST API (`/api/states`,
`/api/services/<domain>/<service>`) with a long-lived access token
(HA_URL and HA_TOKEN in .env). Off unless `[home_assistant] enabled`.

Safety, in order:
- Only entities in `[home_assistant] domains` are listed or touched, and
  only the fixed services in ACTIONS are ever called: never
  `homeassistant.restart` or any service Claude names itself. Scripts,
  scenes and automations run only if their entity id is in
  `[home_assistant] scripts`.
- Locks, alarm panels, garage doors and gates (by device class or name)
  and `secure_entities` always ask for a spoken yes first. Unlocking or
  opening one is refused outright while untrusted text (web, email) is in
  the conversation, and is never done for a whole room at once.
- Every other change asks first only when the conversation is tainted
  (the usual confirm_if_untrusted rule).
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import threading
import time
from typing import Any

from kwil import i18n
from kwil.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted

log = logging.getLogger(__name__)

DEFAULT_DOMAINS = ["light", "switch", "fan", "climate", "cover", "media_player",
                   "sensor", "lock"]
READ_ONLY_DOMAINS = {"sensor", "binary_sensor", "weather", "person", "sun"}
RUNNABLE_DOMAINS = {"script": "turn_on", "scene": "turn_on", "automation": "trigger"}
# Always a spoken yes; opening/unlocking refused when tainted.
SECURE_DOMAINS = {"lock", "alarm_control_panel"}
SECURE_COVER_CLASSES = {"garage", "gate", "door"}
# In a switch's or cover's id or name: it may open the house. Names often
# run words together ("Garagedoor", "FrontDoor", "doorlock"), so:
# - garage, gate, 车库: anywhere (a false "navigate" only adds a question);
# - door: anywhere but in "indoor"/"outdoor" or "doorbell";
# - lock: anywhere but after b, c or f ("block", "clock", "flock");
# - deadbolt; 锁 (lock) and 门禁 (door access) anywhere;
# - 门 only as a door: 大门, 车库门, 闸门, or 门 ending the name or a word
#   (后门, "后门 继电器"), never 门口灯 or 门铃.
# A room (area) called "Garage" doesn't count (it would flag every garage
# light): relays named otherwise belong in secure_entities.
SECURE_WORDS = re.compile(r"garage|gate|(?<!in)(?<!out)door(?!bell)|(?<![bcf])lock|deadbolt"
                          r"|车库|锁|门禁|大门|闸门|门(?=\s|$)")
# On a secure entity, everything but these opens the house.
CLOSING_ACTIONS = {"lock", "close", "arm_home", "arm_away", "arm_night"}

# (domain, action) -> service. Nothing else is ever called.
ACTIONS: dict[str, dict[str, str]] = {
    "light": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "switch": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle"},
    "fan": {"turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle",
            "set_fan_speed": "set_percentage"},
    "climate": {"turn_on": "turn_on", "turn_off": "turn_off",
                "set_temperature": "set_temperature",
                "set_hvac_mode": "set_hvac_mode"},
    "cover": {"open": "open_cover", "close": "close_cover", "stop": "stop_cover",
              "toggle": "toggle", "set_position": "set_cover_position"},
    "lock": {"lock": "lock", "unlock": "unlock"},
    "media_player": {"turn_on": "turn_on", "turn_off": "turn_off",
                     "toggle": "toggle", "play": "media_play",
                     "pause": "media_pause", "stop": "media_stop",
                     "next": "media_next_track", "previous": "media_previous_track",
                     "set_volume": "volume_set"},
    "alarm_control_panel": {"arm_home": "alarm_arm_home",
                            "arm_away": "alarm_arm_away",
                            "arm_night": "alarm_arm_night",
                            "disarm": "alarm_disarm"},
}
ALL_ACTIONS = sorted({a for m in ACTIONS.values() for a in m} | {"run"})
HVAC_MODES = ["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]
# Attributes a state answer may carry (no free text such as media titles).
SAFE_ATTRIBUTES = ("unit_of_measurement", "device_class", "brightness",
                   "color_temp_kelvin", "temperature", "current_temperature",
                   "target_temp_high", "target_temp_low", "hvac_action",
                   "min_temp", "max_temp", "percentage", "current_position",
                   "volume_level", "is_volume_muted")
MAX_ENTITIES = 2000   # kept from one /api/states read
MAX_LISTED = 60       # in one ha_list answer
MAX_GROUP = 20        # entities in one area-wide call
_PLAIN_STATE = re.compile(r"^[a-z_]{1,24}$")
MAX_NAME = 60         # characters of a friendly or area name passed on
# A device or room name, not a sentence: short, no sentence punctuation,
# colon, slash or line break. Anything else may be someone's instruction.
_NOT_A_NAME = re.compile(r"[.!?;:/\\\n\r。！？；：]")


def _clean_name(value: Any, fallback: str = "") -> str:
    """One line, at most MAX_NAME characters (an integration can take a
    name from the device itself)."""
    text = " ".join(str(value or "").split()) or fallback
    return text[:MAX_NAME]


def _plain_name(text: str) -> bool:
    return len(text) <= 40 and not _NOT_A_NAME.search(text)


def _names_plain(entities) -> bool:
    return all(_plain_name(e["name"]) and _plain_name(e["area"]) for e in entities)


def plain_http_off_lan(url: str) -> bool:
    """True for an http:// address that isn't on this machine or the home
    network: the token would cross the internet unencrypted."""
    import ipaddress
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    if host == "localhost" or host.endswith((".local", ".lan", ".home.arpa",
                                             ".internal", ".ts.net")):
        return False  # (Tailscale encrypts the link itself)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." in host  # a public-looking name; a bare LAN name is fine
    tailscale = ip in ipaddress.ip_network("100.64.0.0/10")
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or tailscale)


class HAError(Exception):
    """A failure to say out loud: `key` is an i18n key."""

    def __init__(self, key: str, detail: str = ""):
        super().__init__(detail or key)
        self.key = key


def _norm(text: str) -> str:
    text = str(text or "").lower().replace("_", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(the|my|a|an)\b", " ", text)
    return " ".join(text.split())


def _tokens(text: str) -> set[str]:
    out = set()
    for word in _norm(text).split():
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # "lights" -> "light"
        out.add(word)
    return out


def _token_in(word: str, words: set[str]) -> bool:
    return word in words or any(
        difflib.SequenceMatcher(None, word, w).ratio() >= 0.85 for w in words)


def _plain(value: Any) -> bool:
    """A number or a one-word state ("on", "locked"): not someone's text."""
    if isinstance(value, (int, float)) or value is None:
        return True
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return bool(_PLAIN_STATE.match(str(value)))


class HomeAssistant:
    def __init__(self, url: str | None = None, token: str | None = None,
                 domains=None, scripts=None, secure_entities=None,
                 cache_seconds: float = 30, timeout: float = 10,
                 session=None, clock=time.monotonic):
        self.url = (url or os.environ.get("HA_URL", "")).rstrip("/")
        self.token = token or os.environ.get("HA_TOKEN", "")
        # a TOML string by mistake must not become a list of letters
        self.domains = set([domains] if isinstance(domains, str)
                           else (domains if domains is not None else DEFAULT_DOMAINS))
        self.scripts = set([scripts] if isinstance(scripts, str) else (scripts or []))
        self.secure_entities = set([secure_entities] if isinstance(secure_entities, str)
                                   else (secure_entities or []))
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self._session = session
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict]] | None = None
        # what this thread's last spoken yes covered: (action, entity ids)
        self._confirmed = threading.local()

    # ----- HTTP -----
    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _request(self, method: str, path: str, body: dict | None = None,
                 admin_only: bool = False):
        if not self.url or not self.token:
            raise HAError("ha_not_set_up")
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        try:
            resp = self.session.request(method, self.url + path, headers=headers,
                                        json=body, timeout=self.timeout,
                                        allow_redirects=False)
        except Exception as e:  # requests' ConnectionError, Timeout, ...
            log.warning("Home Assistant unreachable: %s", type(e).__name__)
            raise HAError("ha_down", type(e).__name__) from None
        status = resp.status_code
        if status in (401, 403):
            if admin_only:  # expected with a non-admin token
                log.debug("Home Assistant: HTTP %s from an admin-only API", status)
            else:
                log.warning("Home Assistant refused the token (HTTP %s)", status)
            raise HAError("ha_auth", f"HTTP {status}")
        if status == 404:
            raise HAError("ha_not_found", "HTTP 404")
        if status >= 400 or 300 <= status < 400:
            log.warning("Home Assistant answered HTTP %s to a %s", status, method)
            raise HAError("ha_down" if status >= 500 else "ha_rejected", f"HTTP {status}")
        return resp

    # ----- entities -----
    def _areas(self) -> dict[str, str]:
        """entity_id -> area name, from a template (the REST API's states
        carry no area). Older installs, and a non-admin token (the
        template API is admin-only): no areas."""
        template = ("{% for s in states %}{{ s.entity_id }}\t"
                    "{{ area_name(s.entity_id) or '' }}\n{% endfor %}")
        try:
            text = self._request("POST", "/api/template", {"template": template},
                                 admin_only=True).text
        except HAError as e:
            if e.key in ("ha_down", "ha_not_set_up"):
                raise
            # /api/states just worked with this token, so a 401/403 here
            # only means "not an admin": go on without areas
            return {}
        areas = {}
        for line in text.splitlines():
            entity, _, area = line.partition("\t")
            if entity.strip() and area.strip():
                areas[entity.strip()] = area.strip()
        return areas

    def entities(self, fresh: bool = False) -> list[dict]:
        """Entities in allowed domains (and allowlisted scripts), cached
        for `cache_seconds`."""
        with self._lock:
            now = self.clock()
            if (not fresh and self._cache is not None
                    and now - self._cache[0] < self.cache_seconds):
                return self._cache[1]
        try:
            states = self._request("GET", "/api/states").json()
        except ValueError:  # not JSON: not Home Assistant's API
            raise HAError("ha_rejected", "not JSON") from None
        areas = self._areas()
        found = []
        for s in states if isinstance(states, list) else []:
            entity_id = str(s.get("entity_id", ""))
            domain = entity_id.partition(".")[0]
            if domain not in self.domains and entity_id not in self.scripts:
                continue
            attrs = s.get("attributes") or {}
            found.append({"entity_id": entity_id, "domain": domain,
                          "name": _clean_name(attrs.get("friendly_name"), entity_id),
                          "area": _clean_name(areas.get(entity_id)),
                          "state": s.get("state"), "attributes": attrs})
            if len(found) >= MAX_ENTITIES:
                break
        with self._lock:
            self._cache = (self.clock(), found)
        log.info("Home Assistant: %d entities", len(found))
        return found

    def is_secure(self, entity: dict) -> bool:
        if entity["domain"] in SECURE_DOMAINS or entity["entity_id"] in self.secure_entities:
            return True
        if (entity["domain"] == "cover" and
                entity["attributes"].get("device_class") in SECURE_COVER_CLASSES):
            return True
        text = f"{entity['entity_id']} {entity['name']}".lower().replace("_", " ")
        return (entity["domain"] in ("cover", "switch", "button")
                and SECURE_WORDS.search(text) is not None)

    @staticmethod
    def _in_area(pool: list[dict], area: str) -> list[dict]:
        """The entities of `pool` in a spoken room: the exact room when any
        entity has it ("Bedroom 1" never brings in "Bedroom 2"), else a
        close spelling, else (no area info) the room's name in the name."""
        want = _norm(area)
        exact = [e for e in pool if _norm(e["area"]) == want]
        if exact:
            return exact
        return [e for e in pool
                if (_norm(e["area"]) and difflib.SequenceMatcher(
                    None, _norm(e["area"]), want).ratio() >= 0.85)
                or (not e["area"] and want in _norm(e["name"]))]

    def match(self, name: str, domain: str | None = None,
              loose_secure: bool = True) -> list[dict]:
        """Entities a spoken name means, best first. More than one = ask.
        loose_secure=False (control): locks and garage doors match only
        exactly or by all their words, never by spelling alone."""
        pool = [e for e in self.entities() if not domain or e["domain"] == domain]
        query = _norm(name)
        if not query:
            return []
        for e in pool:
            if e["entity_id"] == name.strip():
                return [e]
        texts = [(e, [_norm(e["name"]), _norm(e["entity_id"].partition(".")[2]),
                      _norm(f"{e['area']} {e['name']}")]) for e in pool]
        exact = [e for e, t in texts if query in t]
        if exact:
            return exact
        words = _tokens(query)
        subset = [e for e, t in texts
                  if any(words and all(_token_in(w, _tokens(x)) for w in words)
                         for x in t)]
        if subset:
            return subset
        scored = []
        for e, t in texts:
            if not loose_secure and self.is_secure(e):
                continue
            score = max(max(difflib.SequenceMatcher(None, query, x).ratio()
                            for x in t if x),
                        0.9 if any(query in x for x in t if len(query) >= 2) else 0)
            scored.append((score, e))
        best = max((s for s, _ in scored), default=0.0)
        if best < 0.6:
            return []
        return [e for s, e in sorted(scored, key=lambda p: -p[0]) if s >= best - 0.05]

    @staticmethod
    def _brief(entity: dict) -> dict:
        out = {"entity_id": entity["entity_id"], "name": entity["name"]}
        if entity["area"]:
            out["area"] = entity["area"]
        return out

    # ----- tools -----
    def _fail(self, e: HAError, **values) -> dict:
        return {"status": "error", "error": e.key,
                "message": i18n.t(e.key, **values) if values else i18n.t(e.key)}

    def list_entities(self, area: str | None = None, domain: str | None = None) -> dict:
        try:
            pool = self.entities()
        except HAError as e:
            return self._fail(e)
        if domain:
            pool = [e for e in pool if e["domain"] == domain]
        if area:
            pool = self._in_area(pool, area)
        pool = sorted(pool, key=lambda e: (e["area"], e["domain"], e["name"]))
        items, untrusted = [], not _names_plain(pool[:MAX_LISTED])
        for e in pool[:MAX_LISTED]:
            item = {**self._brief(e), "state": e["state"]}
            untrusted = untrusted or not _plain(e["state"])
            items.append(item)
        result = {"count": len(pool), "entities": items}
        if len(pool) > MAX_LISTED:
            result["more"] = len(pool) - MAX_LISTED
        return mark_untrusted(result, untrusted)

    def get_state(self, name: str | None = None, entity_id: str | None = None,
                  domain: str | None = None) -> dict:
        try:
            found = self.match(entity_id or name or "", domain)
        except HAError as e:
            return self._fail(e)
        if not found:
            return {"status": "not_found", "message": i18n.t("ha_no_match",
                                                             name=name or entity_id or "")}
        if len(found) > 1:
            return self._ambiguous(found)
        e = found[0]
        result = {**self._brief(e), "state": e["state"]}
        attrs = {k: e["attributes"][k] for k in SAFE_ATTRIBUTES if k in e["attributes"]}
        if attrs:
            result["attributes"] = attrs
        return mark_untrusted(result, not (_plain(e["state"]) and _names_plain([e])))

    def _ambiguous(self, found: list[dict]) -> dict:
        names = [f"{e['name']} ({e['area']})" if e["area"] else e["name"]
                 for e in found[:8]]
        joiner = "、" if i18n.is_zh() else ", "
        return mark_untrusted(
            {"status": "ambiguous", "matches": [self._brief(e) for e in found[:8]],
             "message": i18n.t("ha_which", names=joiner.join(names))},
            not _names_plain(found[:8]))

    def _targets(self, action: str, name: str | None, entity_id: str | None,
                 area: str | None, domain: str | None) -> tuple[list[dict], dict | None]:
        """(entities, None), or ([], an answer for Claude)."""
        wanted = domain or (entity_id.partition(".")[0] if entity_id and "." in entity_id
                            else None)
        if (wanted and wanted not in self.domains
                and not (entity_id and entity_id in self.scripts)):
            return [], {"status": "refused",
                        "message": i18n.t("ha_domain_off", domain=wanted)}
        if area and not (name or entity_id):
            if not domain:
                return [], {"status": "error", "error": "need_domain",
                            "message": "Give a domain with area (e.g. light)."}
            pool = self._in_area([e for e in self.entities()
                                  if e["domain"] == domain], area)
            if not pool:
                return [], {"status": "not_found",
                            "message": i18n.t("ha_no_match", name=area)}
            safe = [e for e in pool if not self.is_secure(e)]
            if not safe:  # locks, garage doors: one at a time, by name
                return [], {"status": "refused", "message": i18n.t("ha_one_at_a_time")}
            return safe[:MAX_GROUP], None
        query = entity_id or name or ""
        found = self.match(query, domain, loose_secure=False)
        if not found:
            return [], {"status": "not_found",
                        "message": i18n.t("ha_no_match", name=query)}
        if len(found) > 1:
            return [], self._ambiguous(found)
        return found, None

    def _check(self, action: str, targets: list[dict]) -> tuple[str, str] | dict:
        """(domain, service), or an answer refusing the call."""
        domain = targets[0]["domain"]
        if any(t["domain"] != domain for t in targets):
            return {"status": "error", "message": "Mixed domains."}
        if domain in RUNNABLE_DOMAINS:
            if action != "run" or any(t["entity_id"] not in self.scripts for t in targets):
                return {"status": "refused", "message": i18n.t("ha_domain_off", domain=domain)}
            return domain, RUNNABLE_DOMAINS[domain]
        if domain not in self.domains:
            return {"status": "refused", "message": i18n.t("ha_domain_off", domain=domain)}
        if domain in READ_ONLY_DOMAINS:
            return {"status": "refused", "message": i18n.t("ha_read_only")}
        service = ACTIONS.get(domain, {}).get(action)
        if service is None:
            return {"status": "refused",
                    "message": i18n.t("ha_unsupported", action=action, domain=domain),
                    "actions": sorted(ACTIONS.get(domain, {}))}
        return domain, service

    def _opens(self, action: str, targets: list[dict]) -> bool:
        return action not in CLOSING_ACTIONS and any(self.is_secure(t) for t in targets)

    def _payload(self, domain: str, service: str, targets: list[dict],
                 values: dict) -> dict | str:
        ids = [t["entity_id"] for t in targets]
        body: dict = {"entity_id": ids[0] if len(ids) == 1 else ids}

        def number(key, low, high):
            value = values.get(key)
            if value is None:
                return None
            value = float(value)
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            return int(value) if value == int(value) else value

        if domain == "light" and service == "turn_on":
            pct = number("brightness_pct", 0, 100)
            if pct == 0:
                return "turn_off"  # "set the lights to 0%"
            if pct is not None:
                body["brightness_pct"] = pct
            kelvin = number("color_temp_kelvin", 1500, 9000)
            if kelvin is not None:
                body["color_temp_kelvin"] = kelvin
        elif domain == "fan" and service in ("turn_on", "set_percentage"):
            pct = number("percentage", 0, 100)
            if pct is not None:
                body["percentage"] = pct
            elif service == "set_percentage":
                raise ValueError("percentage is required")
        elif domain == "climate" and service == "set_temperature":
            temp = values.get("temperature")
            if temp is None:
                raise ValueError("temperature is required")
            attrs = targets[0]["attributes"]
            body["temperature"] = number("temperature", float(attrs.get("min_temp", -40)),
                                         float(attrs.get("max_temp", 120)))
            if values.get("hvac_mode"):
                body["hvac_mode"] = self._hvac(values["hvac_mode"])
        elif domain == "climate" and service == "set_hvac_mode":
            if not values.get("hvac_mode"):
                raise ValueError("hvac_mode is required")
            body["hvac_mode"] = self._hvac(values["hvac_mode"])
        elif domain == "cover" and service == "set_cover_position":
            pos = number("position", 0, 100)
            if pos is None:
                raise ValueError("position is required")
            body["position"] = pos
        elif domain == "media_player" and service == "volume_set":
            vol = number("volume_pct", 0, 100)
            if vol is None:
                raise ValueError("volume_pct is required")
            body["volume_level"] = round(vol / 100, 2)
        return body

    @staticmethod
    def _hvac(mode: str) -> str:
        mode = str(mode).strip().lower().replace(" ", "_")
        if mode not in HVAC_MODES:
            raise ValueError(f"hvac_mode must be one of {HVAC_MODES}")
        return mode

    def control(self, action: str, name: str | None = None,
                entity_id: str | None = None, area: str | None = None,
                domain: str | None = None, **values) -> dict:
        confirmed = getattr(self._confirmed, "value", None)
        self._confirmed.value = None  # a yes covers one call only
        try:
            targets, answer = self._targets(action, name, entity_id, area, domain)
            if answer is not None:
                return answer
            checked = self._check(action, targets)
            if isinstance(checked, dict):
                return checked
            ha_domain, service = checked
            ids = [t["entity_id"] for t in targets]
            if self._opens(action, targets) and UNTRUSTED_CONTEXT.get():
                log.info("Home Assistant: refused %s.%s (untrusted content)",
                         ha_domain, service)
                return {"status": "refused", "message": i18n.t("ha_refused_untrusted")}
            secure = any(self.is_secure(t) for t in targets)
            if (secure and confirmed and confirmed[0] == action
                    and confirmed[1] is None):
                # Kyle said yes, but to a question asked while Home
                # Assistant didn't answer: that never covers a lock
                return {"status": "refused", "message": i18n.t("ha_ask_again")}
            if not self._was_confirmed(confirmed, action, ids, secure,
                                       name, entity_id, area, domain):
                # describe_for_control asks first; without that yes, never
                return {"status": "refused", "message": i18n.t("ha_needs_yes")}
            body = self._payload(ha_domain, service, targets, values)
            if body == "turn_off":
                service, body = "turn_off", {"entity_id": ids[0] if len(ids) == 1 else ids}
            self._request("POST", f"/api/services/{ha_domain}/{service}", body)
        except HAError as e:
            return self._fail(e)
        with self._lock:
            self._cache = None  # the next read shows the change
        log.info("Home Assistant: %s.%s on %d entities", ha_domain, service, len(ids))
        log.debug("Home Assistant: %s.%s %s", ha_domain, service, ids)
        return mark_untrusted({"status": "done", "service": f"{ha_domain}.{service}",
                               "entities": [self._brief(t) for t in targets]},
                              not _names_plain(targets))

    @staticmethod
    def _was_confirmed(confirmed, action, ids, secure, *spoken) -> bool:
        """Did this call's spoken yes cover it? Locks and garage doors need
        a yes for these very entities. With untrusted text in the
        conversation, everything needs a yes: for these entities, or (when
        Home Assistant couldn't be read while asking) for these words."""
        if not (secure or UNTRUSTED_CONTEXT.get()):
            return True
        if not confirmed or confirmed[0] != action:
            return False
        if confirmed[1] == ids:
            return True
        return not secure and confirmed[1] is None and confirmed[2] == spoken

    def describe_for_control(self, tool_input: dict) -> str | None:
        """The spoken question before a change, or None for no question:
        locks, garage doors and alarms always ask; anything else only
        with untrusted text in the conversation. Opening one while
        tainted gets no question: control() refuses it."""
        self._confirmed.value = None
        action = tool_input.get("action", "")
        tainted = UNTRUSTED_CONTEXT.get()
        spoken = tuple(tool_input.get(k) for k in ("name", "entity_id", "area", "domain"))
        try:
            targets, answer = self._targets(action, *spoken)
        except HAError:
            if not tainted:
                return None  # control() says what went wrong
            # Home Assistant didn't answer: ask anyway, about the words
            # said (control() won't run a lock or garage door on this yes)
            self._confirmed.value = (action, None, spoken)
            what = next((str(v) for v in spoken[:3] if v), "")
            return i18n.t("ha_ask_change", name=what, action=action.replace("_", " "))
        if answer is not None or isinstance(self._check(action, targets), dict):
            return None  # nothing will run
        secure = any(self.is_secure(t) for t in targets)
        if secure and tainted and self._opens(action, targets):
            return None  # refused in control()
        if not secure and not tainted:
            return None
        self._confirmed.value = (action, [t["entity_id"] for t in targets], spoken)
        joiner = "、" if i18n.is_zh() else ", "
        names = joiner.join(t["name"] for t in targets[:5])
        if len(targets) > 5:
            names += i18n.t("ha_and_more", n=len(targets) - 5)
        key = {"unlock": "ha_ask_unlock", "lock": "ha_ask_lock", "open": "ha_ask_open",
               "close": "ha_ask_close", "disarm": "ha_ask_disarm",
               "arm_home": "ha_ask_arm", "arm_away": "ha_ask_arm",
               "arm_night": "ha_ask_arm", "turn_on": "ha_ask_on",
               "turn_off": "ha_ask_off"}.get(action, "ha_ask_change")
        return i18n.t(key, name=names, action=action.replace("_", " "))

    def tools(self) -> list[Tool]:
        domains = sorted(self.domains | {s.partition(".")[0] for s in self.scripts})
        return [
            Tool("ha_list",
                 "List Kyle's smart home devices (Home Assistant) with their state, "
                 "optionally only one room (area) or kind (domain).",
                 {"type": "object", "properties": {
                     "area": {"type": "string", "description": "Room, e.g. 'bedroom'"},
                     "domain": {"type": "string", "enum": domains}}},
                 self.list_entities, private=True),
            Tool("ha_state",
                 "Read one smart home device's state, e.g. 'is the front door "
                 "locked', 'what is the thermostat set to'. Name it as Kyle "
                 "said it; if several match, ask him which one.",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "Spoken name, "
                              "e.g. 'front door' or 'living room temperature'"},
                     "entity_id": {"type": "string"},
                     "domain": {"type": "string", "enum": domains}}},
                 self.get_state, private=True),
            Tool("ha_control",
                 "Control a smart home device in Home Assistant. Give name "
                 "(as Kyle said it) or entity_id; for every device of one "
                 "kind in a room ('the bedroom lights') give area and domain "
                 "instead. Brightness: action turn_on with brightness_pct. "
                 "Thermostat: set_temperature with temperature (in the "
                 "thermostat's unit). Fan speed: set_fan_speed with "
                 "percentage. Media volume: set_volume with volume_pct. "
                 "'run' starts an allowed script or scene. If the answer is "
                 "ambiguous, ask Kyle which one; never guess. Locks, garage "
                 "doors and alarms ask him first.",
                 {"type": "object", "properties": {
                     "action": {"type": "string", "enum": ALL_ACTIONS},
                     "name": {"type": "string"},
                     "entity_id": {"type": "string"},
                     "area": {"type": "string"},
                     "domain": {"type": "string", "enum": domains},
                     "brightness_pct": {"type": "number", "minimum": 0, "maximum": 100},
                     "color_temp_kelvin": {"type": "number", "minimum": 1500,
                                           "maximum": 9000},
                     "temperature": {"type": "number"},
                     "hvac_mode": {"type": "string", "enum": HVAC_MODES},
                     "percentage": {"type": "number", "minimum": 0, "maximum": 100},
                     "position": {"type": "number", "minimum": 0, "maximum": 100},
                     "volume_pct": {"type": "number", "minimum": 0, "maximum": 100}},
                  "required": ["action"]},
                 self.control, confirm_prompt=self.describe_for_control,
                 private=True),
        ]
