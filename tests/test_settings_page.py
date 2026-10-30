"""The gear's menu as a full-screen Settings page (kwil/web/index.html):
every element a script looks up by id is still there exactly once, inside
the <details class="menu"> that twelve scripts query, and the order the
scripts and tests rely on holds."""

import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "kwil" / "web" / "index.html").read_text(encoding="utf-8")

# the ids app.js, history.js, family.js, brief.js, memory.js, projects.js,
# reminders.js, voice-picker.js, connections.js and friends look up
STABLE_IDS = (
    "settings-title", "settings-done",
    "phone-voice", "mute", "voice-picker", "voice-select", "voice-paid", "voice-preview",
    "voice-custom-row", "voice-custom", "voice-custom-use", "voice-status",
    "memory-open", "projects-open",
    "push-toggle", "reminders-open", "push-status",
    "connections", "conn-connected", "conn-available", "conn-google", "google-status",
    "google-connect", "google-disconnect", "conn-notion", "notion-status", "notion-detail",
    "notion-connect", "notion-disconnect", "conn-canvas", "student", "conn-home_assistant",
    "conn-spotify", "spotify-status", "spotify-connect", "conn-anthropic", "api-key",
    "api-key-state", "api-key-cost", "api-key-form", "api-key-input", "api-key-save",
    "api-key-remove",
    "incognito-row", "incognito", "account-self", "account-export", "account-delete-open",
    "account-delete-form", "account-delete-confirm", "account-status",
    "family", "family-list", "invite-open", "invite-form", "invite-name", "invite-language",
    "invite-tier", "invite-tz", "invite-status", "invite-link",
    "backends", "backend-mode", "url-pi", "url-pc", "url-cloud", "backends-save",
    "backend-status",
    "logout", "logout-all",
)


def menu() -> str:
    start = PAGE.index('<details class="menu">')
    return PAGE[start:PAGE.index("</details>", start)]


def test_every_stable_id_is_once_and_inside_the_menu():
    body = menu()
    assert '<summary aria-label="Settings">' in body
    assert PAGE.count('<details class="menu">') == 1
    for el in STABLE_IDS:
        assert PAGE.count(f'id="{el}"') == 1, el
        assert f'id="{el}"' in body, el


def test_the_order_scripts_rely_on_holds():
    body = menu()
    assert body.index('id="google-connect"') < body.index('id="notion-connect"')
    assert body.index('<section id="connections"') < body.index('<section id="api-key"')
    # the title and Done come first; the sections are wrapped, never nested
    assert body.index('id="settings-title"') < body.index('class="set-sec"')
    for el in ("account-self", "api-key"):
        part = re.search(rf'<section id="{el}"[\s\S]*?</section>', PAGE).group(0)
        assert "<section" not in part[1:], el
    scripts = re.findall(r'<script src="([^"]+)"', PAGE)
    assert scripts.index("/connections.js") < scripts.index("/i18n.js") < scripts.index("/family.js")
    assert scripts.index("/ui.js") < scripts.index("/app.js") < scripts.index("/history.js")
